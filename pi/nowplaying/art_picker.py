"""Candidate aggregator for the album-art picker.

``fetch_candidates`` is an ``AsyncGenerator`` so the HTTP handler can flush
each candidate to the SSE stream the moment it lands instead of blocking
on the slowest source. Per-source 3 s budget, 6 s overall.

Sources:
  1. Discogs master image — needs ``release → master_id`` resolution first.
  2. Discogs release images — bounded by ``MAX_DISCOGS_RELEASE_FETCHES``
     and a ``Semaphore(2)`` to stay polite under the 60 req/min cap.
  3. MusicBrainz / Cover Art Archive — same pathway the default ``/art``
     resolution uses, so the user sees "what we'd pick by default".
  4. Currently-served art (local override or cached CAA file) — always
     emitted first as the "Current" option so "keep current" is one tap.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional, TypedDict
from urllib.parse import urlencode

import aiohttp

from nowplaying import art_overrides, coverart
from nowplaying.discogs import images as dimages
from nowplaying.net_allowlist import is_allowed_upstream

log = logging.getLogger("nowplaying.art_picker")

PER_SOURCE_TIMEOUT_S = 3.0
OVERALL_TIMEOUT_S = 6.0
MAX_DISCOGS_RELEASE_FETCHES = 4
_DISCOGS_CONCURRENCY = asyncio.Semaphore(2)


class Candidate(TypedDict, total=False):
    url: str
    source: str           # "current" | "discogs-master" | "discogs-release" | "caa"
    label: str
    width: int
    height: int
    release_id: int


def _current_url(
    artist: str,
    album: str,
    release_id: Optional[int],
    fallback_url: Optional[str],
    *,
    has_override: bool,
) -> str:
    """URL to fetch the currently-served art. Resolution order:
      1. /art/<rid>?v=current          when release_id is set (vinyl)
      2. /art-by-name?artist=…&album=… when an override exists for
                                       this (artist, album)
      3. fallback_url                  the kiosk's current art_url —
                                       typically Sonos's streaming-service
                                       art proxied through /art-cache/...
                                       Without this, the "Current" tile
                                       would 404 for any streaming track
                                       without a saved override.
    """
    if release_id is not None:
        return f"/art/{release_id}?v=current"
    if has_override:
        return f"/art-by-name?{urlencode({'artist': artist, 'album': album})}&v=current"
    return fallback_url or ""


async def _emit_current(
    queue: "asyncio.Queue[Optional[Candidate]]",
    artist: str,
    album: str,
    release_id: Optional[int],
    current_url: Optional[str],
) -> None:
    """Always-first candidate: whatever the orchestrator's art handler
    would serve right now. Lets the user pick "Keep current" without
    round-tripping Discogs.

    For the by-name path, ``current_url`` is the kiosk's currently
    rendered ``art_url`` — typically Sonos's streaming-service art —
    used as the displayable URL when no override has been saved yet."""
    ov = art_overrides.get(artist, album)
    label = "Current (user pick)" if ov else "Current (default)"
    url = _current_url(
        artist, album, release_id, current_url, has_override=ov is not None,
    )
    if not url:
        # Streaming track with no Discogs match, no override, AND no
        # fallback art_url. Nothing displayable for "Current" — skip.
        return
    payload: Candidate = {
        "url": url,
        "source": "current",
        "label": label,
    }
    if release_id is not None:
        payload["release_id"] = release_id
    await queue.put(payload)


async def _caa_url_exists(
    session: Optional[aiohttp.ClientSession],
    url: str,
    timeout_s: float = 2.0,
) -> bool:
    """HEAD-check a CAA URL. CAA itself returns a 307/302 to archive.org;
    we only need the redirect chain to terminate at a 200 (the file
    exists). Returns True only on a final 200; treats redirects as
    intermediate and any other status / network error as "doesn't exist."

    Why this exists: `coverart.search_release_candidates` returns every
    MusicBrainz release MBID matching artist+album, but CAA is a separate
    volunteer database — some MBIDs have no front-cover upload. Without
    this filter the picker emits dead-link tiles that 404 in the browser.

    ``session`` may be ``None`` in unit tests that pass ``session=None``
    to ``fetch_candidates``; in that case we optimistically allow the
    candidate through (the test harness typically stubs this function).
    """
    if session is None:
        return True
    # Defense in depth: only HEAD-check URLs on our outbound allowlist.
    # In practice every URL here is built from
    # `https://coverartarchive.org/...` literals in `_build_caa_proposed`,
    # so this is a belt-and-suspenders guard against future callers
    # passing in user-influenced URLs.
    if not is_allowed_upstream(url):
        log.debug("art_picker: refusing CAA HEAD on non-allowlisted url: %s", url)
        return False
    try:
        async with session.head(  # skylos: ignore SKY-D216 — url gated by is_allowed_upstream() above
            url,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
            allow_redirects=True,
        ) as resp:
            return resp.status == 200
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.debug("art_picker: CAA HEAD %s failed: %r", url, e)
        return False


async def _caa_search_candidates(
    artist: str, album: str,
) -> list[tuple[str, Optional[str]]]:
    """Multi-release CAA candidate search with the per-source timeout
    envelope. Returns ``[]`` on timeout or any upstream error."""
    try:
        return await asyncio.wait_for(
            coverart.search_release_candidates(
                artist, album, timeout_s=PER_SOURCE_TIMEOUT_S, limit=6,
            ),
            timeout=PER_SOURCE_TIMEOUT_S + 0.5,
        )
    except asyncio.TimeoutError:
        log.debug("art_picker: caa search_release_candidates timed out")
        return []
    except Exception as e:  # pragma: no cover - defensive
        log.debug("art_picker: caa search_release_candidates failed: %r", e)
        return []


async def _caa_fallback_candidate(
    artist: str, album: str,
) -> list[tuple[str, Optional[str]]]:
    """Single-release fallback when the multi-search returns nothing.
    Preserves the original "always try at least one MBID" behaviour."""
    try:
        result = await asyncio.wait_for(
            coverart.fetch_release_mbid(
                artist, album, timeout_s=PER_SOURCE_TIMEOUT_S,
            ),
            timeout=PER_SOURCE_TIMEOUT_S + 0.5,
        )
    except asyncio.TimeoutError:
        log.debug("art_picker: caa fetch_release_mbid timed out")
        return []
    except Exception as e:  # pragma: no cover - defensive
        log.debug("art_picker: caa fetch_release_mbid fallback failed: %r", e)
        return []
    if not result:
        return []
    release_mbid, rg_mbid = result
    if not release_mbid:
        return []
    return [(release_mbid, rg_mbid)]


def _build_caa_proposed(
    mbids: list[tuple[str, Optional[str]]],
) -> list[Candidate]:
    """Build the CAA candidate URL list from MBIDs.

    Uses the 1200px CAA variant rather than /front-500. The kiosk renders
    art at ~820px on a 1080p panel — /front-500 was a 1.6x upscale that
    looked visibly soft; /front-1200 is a slight downsample that's crisp.
    The default-art download path (coverart.fetch_cover_art_release) has
    always used /front-1200, so this brings the override picker into
    parity. Preview thumbnails over the local network add ~100 KB/tile,
    which is invisible to the user.
    """
    seen_rgs: set[str] = set()
    proposed: list[Candidate] = []
    for rid, rg in mbids:
        proposed.append({
            "url": f"https://coverartarchive.org/release/{rid}/front-1200",
            "source": "caa",
            "label": "Cover Art Archive",
        })
        if rg and rg not in seen_rgs:
            seen_rgs.add(rg)
            proposed.append({
                "url": f"https://coverartarchive.org/release-group/{rg}/front-1200",
                "source": "caa",
                "label": "Cover Art Archive (release group)",
            })
    return proposed


async def _emit_caa(
    queue: "asyncio.Queue[Optional[Candidate]]",
    artist: str,
    album: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> None:
    """Emit one CAA candidate per MusicBrainz release matching
    (artist, album). Different MB releases of the same album often have
    distinct front covers in CAA (anniversary editions, regional
    pressings, expanded reissues) — exactly the variety streaming /
    AirPlay tracks need, since without a Discogs match this is the
    only source of alternates.

    Falls back to the single-release path (`fetch_release_mbid`) if
    the multi-release search misses, so we never regress to "zero CAA
    candidates" relative to the prior behaviour.

    Each candidate URL is HEAD-checked before being queued so the
    kiosk's picker never receives a dead-link tile (some MBIDs in
    MusicBrainz have no front-cover upload in CAA).
    """
    candidates = await _caa_search_candidates(artist, album)
    if not candidates:
        candidates = await _caa_fallback_candidate(artist, album)
    if not candidates:
        return

    proposed = _build_caa_proposed(candidates)
    # HEAD-check concurrently; the slowest CAA HEAD usually finishes in
    # well under a second.
    exists_flags = await asyncio.gather(
        *[_caa_url_exists(session, c["url"]) for c in proposed],
        return_exceptions=False,
    )
    for cand, exists in zip(proposed, exists_flags):
        if exists:
            await queue.put(cand)
        else:
            log.debug(
                "art_picker: CAA candidate skipped (no upload): %s",
                cand["url"],
            )


async def _emit_discogs_master(
    queue: "asyncio.Queue[Optional[Candidate]]",
    release_id: int,
    session: aiohttp.ClientSession,
) -> Optional[int]:
    async with _DISCOGS_CONCURRENCY:
        try:
            master_id = await asyncio.wait_for(
                dimages.resolve_master_id(session, release_id),
                timeout=PER_SOURCE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.debug(
                "art_picker: discogs resolve_master_id(%s) timed out", release_id,
            )
            return None
    if not master_id:
        return None
    async with _DISCOGS_CONCURRENCY:
        try:
            imgs = await asyncio.wait_for(
                dimages.fetch_master_images(session, master_id),
                timeout=PER_SOURCE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.debug(
                "art_picker: discogs fetch_master_images(%s) timed out",
                master_id,
            )
            return master_id
    for img in imgs[:1]:  # master primary only — the master "canonical" art
        await queue.put({
            "url": img["url"],
            "source": "discogs-master",
            "label": "Discogs master",
            **{k: v for k, v in img.items() if k in ("width", "height")},
        })
    # Master payload also includes secondaries; the user can still see
    # release-level scans below.
    for img in imgs[1:MAX_DISCOGS_RELEASE_FETCHES + 1]:
        await queue.put({
            "url": img["url"],
            "source": "discogs-master",
            "label": "Discogs master (alt)",
            **{k: v for k, v in img.items() if k in ("width", "height")},
        })
    return master_id


async def _emit_discogs_release(
    queue: "asyncio.Queue[Optional[Candidate]]",
    release_id: int,
    session: aiohttp.ClientSession,
) -> None:
    async with _DISCOGS_CONCURRENCY:
        try:
            imgs = await asyncio.wait_for(
                dimages.fetch_release_images(session, release_id),
                timeout=PER_SOURCE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.debug(
                "art_picker: discogs fetch_release_images(%s) timed out",
                release_id,
            )
            return
    for img in imgs[:MAX_DISCOGS_RELEASE_FETCHES]:
        await queue.put({
            "url": img["url"],
            "source": "discogs-release",
            "label": "Discogs release",
            "release_id": release_id,
            **{k: v for k, v in img.items() if k in ("width", "height")},
        })


async def _run_candidate_sources(
    queue: "asyncio.Queue[Optional[Candidate]]",
    artist: str,
    album: str,
    *,
    release_id: Optional[int],
    current_url: Optional[str],
    session: aiohttp.ClientSession,
) -> None:
    """Drive every candidate source under ``OVERALL_TIMEOUT_S`` and emit
    a sentinel ``None`` when done. Always puts the sentinel even on
    timeout or cancellation so the consumer loop terminates cleanly."""
    try:  # skylos: ignore SKY-L004 — try/finally covers the full driver lifecycle so the sentinel always lands
        await _emit_current(queue, artist, album, release_id, current_url)
        tasks = [
            asyncio.create_task(_emit_caa(queue, artist, album, session=session)),
        ]
        if release_id is not None:
            tasks.append(
                asyncio.create_task(_emit_discogs_master(queue, release_id, session))
            )
            tasks.append(
                asyncio.create_task(_emit_discogs_release(queue, release_id, session))
            )
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=OVERALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            for t in tasks:
                t.cancel()
    finally:
        await queue.put(None)  # sentinel — "done"


async def _drain_runner(runner_task: "asyncio.Task[None]") -> None:
    """Cancel-and-await the runner so the generator's `finally` cannot
    leak it. Swallows the expected ``CancelledError`` and logs any
    other exception that surfaced through ``return_exceptions=True``."""
    if runner_task.done():
        return
    runner_task.cancel()
    try:
        await runner_task
    except asyncio.CancelledError:
        log.debug("art_picker: runner task cancelled cleanly")
    except Exception as e:  # pragma: no cover - defensive
        log.debug("art_picker: runner task exited with error: %r", e)


async def fetch_candidates(
    artist: str,
    album: str,
    release_id: Optional[int] = None,
    *,
    session: aiohttp.ClientSession,
    current_url: Optional[str] = None,
) -> AsyncGenerator[Candidate, None]:
    """Yield candidates as they resolve. Caller iterates with
    ``async for cand in fetch_candidates(...)`` and dispatches each one
    over the SSE stream. Returns when all sources have completed or the
    overall timeout has fired.

    ``release_id`` is optional: streaming/AirPlay tracks without a
    Discogs match still get CAA candidates (keyed by artist+album)
    plus a "Current" candidate served by /art-by-name. Discogs master
    + release scans only fire when release_id is set."""
    queue: asyncio.Queue[Optional[Candidate]] = asyncio.Queue()
    runner_task = asyncio.create_task(
        _run_candidate_sources(
            queue, artist, album,
            release_id=release_id, current_url=current_url, session=session,
        )
    )
    seen: set[str] = set()
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            url = item.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            yield item
    finally:
        await _drain_runner(runner_task)
