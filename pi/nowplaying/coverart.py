"""MusicBrainz Cover Art Archive integration — canonical front-cover art.

Free, open, no API key. MB-only by design (Apple Music + Discogs art tiers
were deliberately stripped in `art-musicbrainz-only`).

Usage:
    from nowplaying import coverart
    res = await coverart.fetch_release_mbid("The Beatles", "Rubber Soul")
    if res:
        release_mbid, rg_mbid = res
        data, outcome = await coverart.fetch_cover_art(release_mbid)
        if data is None and rg_mbid:
            data, outcome = await coverart.fetch_cover_art_release_group(rg_mbid)

`fetch_cover_art` / `fetch_cover_art_release_group` return a tuple
`(bytes | None, outcome)` where outcome is one of:
  - "ok"        — image bytes returned
  - "missing"   — definitive HTTP 404 from CAA (safe to negative-cache)
  - "transient" — timeout / connection error / 5xx after retries (do
                  NOT stamp negative — the next recognition should try)
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import urllib.parse
from typing import Literal, Optional

import aiohttp

log = logging.getLogger("nowplaying.coverart")

# MusicBrainz requires a descriptive User-Agent with contact info per their
# TOS. The repo URL serves as contact for unattended deployments.
USER_AGENT = "now-playing/1.0 (https://github.com/schuettc/now-playing)"

MB_SEARCH_URL = "https://musicbrainz.org/ws/2/release/"
CAA_FRONT_URL = "https://coverartarchive.org/release/{mbid}/front-{size}"
CAA_RG_FRONT_URL = "https://coverartarchive.org/release-group/{mbid}/front-{size}"

# In-memory negative cache: (artist, album) tuples that we've already looked
# up and got nothing for. Prevents hammering MusicBrainz when the listener
# replays records we already failed to find.
_NEGATIVE_TTL_S = 7 * 24 * 3600
_negative_cache: dict[tuple[str, str], float] = {}

# Outcome for CAA fetches. Surfaced so the orchestrator wrapper can stamp
# the negative cache only on definitive misses (not on transient failures
# like timeouts or 5xx, which are the symptom this PR fixes).
Outcome = Literal["ok", "missing", "transient"]


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _negative_cached(artist: str, album: str) -> bool:
    key = (_norm(artist), _norm(album))
    now = time.time()
    stamp = _negative_cache.get(key)
    if stamp is None:
        return False
    if now - stamp > _NEGATIVE_TTL_S:
        _negative_cache.pop(key, None)
        return False
    return True


def mark_negative(artist: str, album: str) -> None:
    """Public so the populator can stamp the cache only after BOTH the
    normalized-title retry and the release-group fallback have failed
    *with definitive misses* (not transient timeouts).
    """
    _negative_cache[(_norm(artist), _norm(album))] = time.time()


# Internal alias kept for any in-module callers / tests.
_mark_negative = mark_negative


# Parenthetical edition-marker patterns. Each entry is the inner content
# pattern (without surrounding parens). Matched case-insensitively against
# the trimmed inside-paren text — if the whole inside matches, the
# parenthetical group is stripped.
_EDITION_PATTERNS = [
    r"deluxe(?: edition)?",
    r"remaster(?:ed)?(?: \d{4})?",
    r"\d{2,4}th anniversary(?: edition)?",
    r"anniversary(?: edition)?",
    r"mono",
    r"stereo",
    r"\d{4} mix",
    r"expanded(?: edition)?",
    r"special edition",
    r"bonus(?: track)?s?",
    r"disc \d+",
    r"cd \d+",
    r"vol\.? \d+",
]
_EDITION_RE = re.compile(
    r"\s*\((?:" + "|".join(_EDITION_PATTERNS) + r")\)\s*",
    flags=re.IGNORECASE,
)
# Trailing anniversary digit token: e.g. "American Idiot 20", "OK Computer 25".
# Only strip when the integer is plausibly an anniversary marker — 10..75
# inclusive — and only at end-of-string, so "Volume 4" / "Symphony No. 9"
# survive.
_TRAILING_ANNIVERSARY_RE = re.compile(r"\s+(\d{2,3})\s*$")


def _normalize_album_title(album: str) -> str:
    """Strip edition markers and trailing anniversary tokens from an album
    title so we can retry a MusicBrainz search when the literal title misses.

    Returns the album unchanged if no pattern matched (so callers can cheaply
    detect a no-op).
    """
    if not album:
        return album
    out = album
    # Strip recognized parenthetical edition markers (may appear more than
    # once, e.g. "(Remastered 2024) (Deluxe Edition)").
    prev = None
    while prev != out:
        prev = out
        out = _EDITION_RE.sub(" ", out)
    # Trailing anniversary integer (10..75 only).
    m = _TRAILING_ANNIVERSARY_RE.search(out)
    if m:
        try:
            n = int(m.group(1))
        except ValueError:
            n = -1
        if 10 <= n <= 75:
            out = out[: m.start()]
    # Trailing punctuation that breaks MusicBrainz literal search:
    # "Endtroducing....." → "Endtroducing",
    # "(What's The Story) Morning Glory?" → "(What's The Story) Morning Glory",
    # "All My Friends Are Funeral Singers!" → "All My Friends Are Funeral Singers".
    # Strip runs of trailing `.?!` characters. Doesn't touch internal punctuation.
    out = re.sub(r"[.?!]+\s*$", "", out)
    # Collapse whitespace.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _extract_mb_top_hit(
    data: dict,
) -> Optional[tuple[str, Optional[str]]]:
    """Pull (release_mbid, release_group_mbid) from a MusicBrainz search
    JSON payload. Returns None when the response has no usable release."""
    releases = data.get("releases") or []
    if not releases:
        return None
    top = releases[0]
    rid = top.get("id")
    if not rid:
        return None
    rg = (top.get("release-group") or {}).get("id")
    return (rid, rg)


async def _mb_search_once(
    url: str,
    headers: dict,
    *,
    session: aiohttp.ClientSession,
    timeout_s: float,
) -> tuple[Optional[dict], int]:
    """One MB search HTTP attempt. Returns (parsed_json | None, status).

    - status=200 with parsed JSON on success
    - status=404 (None) on definitive miss
    - status=non-200 (None) on other HTTP error
    Raises aiohttp.ClientError / asyncio.TimeoutError on transport failure.
    """
    async with session.get(  # skylos: ignore SKY-D216 — url built from hardcoded musicbrainz.org / coverartarchive.org templates; only MBIDs interpolated
        url, headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    ) as resp:
        if resp.status != 200:
            return None, resp.status
        return await resp.json(), 200


def _build_mb_search_url(artist: str, album: str, *, limit: int = 5) -> str:
    safe_album = album.replace('"', r'\"')
    safe_artist = artist.replace('"', r'\"')
    query = f'release:"{safe_album}" AND artist:"{safe_artist}"'
    return f"{MB_SEARCH_URL}?query={urllib.parse.quote(query)}&fmt=json&limit={limit}"


async def _mb_search_attempt(
    url: str,
    headers: dict,
    *,
    session: aiohttp.ClientSession,
    timeout_s: float,
    is_last: bool,
) -> tuple[Optional[tuple[Optional[tuple[str, Optional[str]]], int]], int]:
    """Run a single _mb_search attempt.

    Returns ``(terminal, status)``:
      - ``terminal`` is None when the caller should retry (status returned
        is still useful for ``last_status`` tracking).
      - ``terminal`` is a ``(hit_or_none, status)`` tuple when the caller
        should return that value as the final result.
    """
    try:
        data, status = await _mb_search_once(
            url, headers, session=session, timeout_s=timeout_s,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        if not is_last:
            log.info("musicbrainz search retrying in 2s after %r", e)
            await asyncio.sleep(2)
            return None, 0
        log.warning("musicbrainz search failed: %r", e)
        return (None, 0), 0

    if status == 404:
        return (None, 404), 404
    if status != 200:
        if not is_last:
            log.info(
                "musicbrainz search status=%d, retrying in 2s", status,
            )
            await asyncio.sleep(2)
            return None, status
        return (None, status), status
    hit = _extract_mb_top_hit(data or {})
    return ((hit, 200) if hit is not None else (None, 200)), 200


async def _mb_search(
    artist: str,
    album: str,
    *,
    session: aiohttp.ClientSession,
    timeout_s: float,
) -> tuple[Optional[tuple[str, Optional[str]]], int]:
    """One MusicBrainz search round with a 2-attempt transient retry.

    Returns ((release_mbid, rg_mbid)|None, http_status). Status is 0 on
    transport error after both attempts.
    """
    url = _build_mb_search_url(artist, album)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    last_status = 0
    attempts = (1, 2)
    for attempt in attempts:
        terminal, status = await _mb_search_attempt(
            url, headers, session=session, timeout_s=timeout_s,
            is_last=(attempt == attempts[-1]),
        )
        last_status = status or last_status
        if terminal is not None:
            return terminal
    return None, last_status


async def search_release_candidates(
    artist: str,
    album: str,
    *,
    timeout_s: float = 5.0,
    limit: int = 6,
) -> list[tuple[str, Optional[str]]]:
    """Return up to `limit` `(release_mbid, release_group_mbid)` matches
    for use as art-picker candidates. Different MusicBrainz releases of
    the same album often have distinct front covers in CAA (original,
    anniversary editions, regional pressings, expanded reissues), which
    is exactly the variety the picker wants to surface.

    Tries the literal artist+album first. If that returns nothing,
    retries with a normalized album title (strips edition markers,
    trailing punctuation) so canonical releases like "Endtroducing..."
    or "(What's The Story) Morning Glory?" still resolve.

    Both inputs are HTML-unescaped before search — Discogs occasionally
    emits literal `&amp;` for ampersands, which would break the search
    query.

    Negative cache is honored but NOT stamped here — that's the
    populator's job after the release-group fallback has also missed.
    """
    if not artist or not album:
        return []
    # HTML-entity unescape both inputs. Discogs has been observed
    # emitting "Jon Langford &amp; His Fancy Men" with the literal
    # entity, which makes MusicBrainz return zero matches.
    artist = html.unescape(artist)
    album = html.unescape(album)
    if _negative_cached(artist, album):
        return []
    candidates = await _search_candidates_once(
        artist, album, timeout_s=timeout_s, limit=limit,
    )
    if candidates:
        return candidates
    # Normalized-album fallback. Catches titles like "Endtroducing..."
    # (trailing dots), "(What's The Story) Morning Glory?" (parens +
    # question mark), "Hail To The Thief (Live Recordings 2003-2009)"
    # (parenthetical-suffix edition markers). See _normalize_album_title.
    normalized = _normalize_album_title(album)
    if normalized != album:
        log.debug(
            "musicbrainz: literal search empty, retrying with normalized "
            "title %r → %r",
            album, normalized,
        )
        return await _search_candidates_once(
            artist, normalized, timeout_s=timeout_s, limit=limit,
        )
    return []


async def _search_candidates_once(
    artist: str,
    album: str,
    *,
    timeout_s: float,
    limit: int,
) -> list[tuple[str, Optional[str]]]:
    """One MusicBrainz search attempt for ``(artist, album)``. Extracted
    so :func:`search_release_candidates` can call it twice (literal,
    then normalized fallback) without duplicating the HTTP plumbing.
    """
    url = _build_mb_search_url(artist, album, limit=limit)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as session:
            async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from module-level MB_SEARCH_URL constant + urllib-quoted query; host is fixed (musicbrainz.org)
                if resp.status != 200:
                    return []
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.debug("musicbrainz search_release_candidates failed: %r", e)
        return []
    out: list[tuple[str, Optional[str]]] = []
    for rel in data.get("releases") or []:
        rid = rel.get("id")
        rg = (rel.get("release-group") or {}).get("id")
        if not rid:
            continue
        out.append((rid, rg))
    # Picker dedups by URL downstream, so any two releases that resolve
    # to the same CAA front cover collapse into one tile automatically.
    return out


async def _mb_search_with_normalized_fallback(
    artist: str,
    album: str,
    *,
    session: aiohttp.ClientSession,
    timeout_s: float,
) -> Optional[tuple[str, Optional[str]]]:
    """Run the literal MB search, then optionally the normalized-title
    fallback. Stamps the negative cache on a definitive 404 from either
    attempt. Returns the hit tuple on success, else None."""
    hit, status = await _mb_search(
        artist, album, session=session, timeout_s=timeout_s,
    )
    if hit is not None:
        return hit
    if status == 404:
        # Definitive MB miss — stamp negative now and don't bother with
        # the normalized retry (the endpoint itself returned not-found).
        mark_negative(artist, album)
        return None
    normalized = _normalize_album_title(album)
    if not normalized or normalized == album:
        return None
    log.info(
        "musicbrainz fallback normalized: %r -> %r", album, normalized,
    )
    hit2, status2 = await _mb_search(
        artist, normalized, session=session, timeout_s=timeout_s,
    )
    if hit2 is not None:
        return hit2
    if status2 == 404:
        mark_negative(artist, album)
    return None


async def fetch_release_mbid(
    artist: str,
    album: str,
    *,
    timeout_s: float = 15.0,
) -> Optional[tuple[str, Optional[str]]]:
    """Search MusicBrainz for a release matching (artist, album). Returns
    `(release_mbid, release_group_mbid)` on hit, else None.

    Issues a second search with a normalized album title if the literal
    title misses — strips edition markers and trailing anniversary digits.

    Negative cache is stamped only on a definitive HTTP 404 from MB
    (structural miss). Empty-result responses and transient errors do NOT
    stamp negative; the caller (the populator) is responsible for marking
    negative only after the release-group cover fallback has also been
    exhausted with a definitive miss.

    Each underlying MB request gets a 2-attempt transient retry (handled
    inside `_mb_search`).
    """
    if not artist or not album:
        return None
    if _negative_cached(artist, album):
        return None

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await _mb_search_with_normalized_fallback(
            artist, album, session=session, timeout_s=timeout_s,
        )


async def fetch_release_recordings(  # skylos: ignore SKY-Q301 SKY-Q306 SKY-Q302 — Why: complexity arises from HTTP error handling + JSON parsing + per-track type coercion in a single fetch; the branches are independent guard clauses that would be more obscure split across helpers
    mbid: str,
    *,
    timeout_s: float = 15.0,
) -> list[dict] | None:
    """Fetch per-track recording data from MusicBrainz for a release MBID.

    Returns a list of ``{"title": str, "duration_seconds": int | None}``
    dicts in the order MusicBrainz returns them (same as the physical
    pressing order — i.e. ``[0]`` is side A track 1, etc.). Returns
    ``None`` on any HTTP / transport failure so callers can distinguish
    "MusicBrainz had no data" (empty list) from "we couldn't reach it"
    (None).

    Used as a duration-enrichment fallback by ``discogs_sync.fetch_detail``
    when Discogs returns empty ``duration`` fields. Position matching is
    done by ordinal index — Discogs catalog ``A1/A2/.../D9`` and the
    MusicBrainz CD release ``1/2/.../31`` may use different position
    strings even though they describe the same album, so the caller
    zips by index and uses a position-count guard for safety.
    """
    if not mbid:
        return None
    url = f"https://musicbrainz.org/ws/2/release/{mbid}?inc=recordings&fmt=json"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from hardcoded musicbrainz.org template; only MBID interpolated
                if resp.status != 200:
                    log.warning(
                        "musicbrainz: release %s returned status=%d",
                        mbid, resp.status,
                    )
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning(
            "musicbrainz: release %s fetch failed: %r", mbid, e,
        )
        return None
    out: list[dict] = []
    for medium in data.get("media") or []:
        for track in medium.get("tracks") or []:  # skylos: ignore SKY-P403 — Why: outer loop is over CD media (typically 1-3); inner over tracks (typically ≤20); total iterations bounded to ~60 per release
            title = track.get("title") or ""
            length_ms = track.get("length")
            duration_s: int | None
            if length_ms is None:
                duration_s = None
            else:
                try:
                    duration_s = int(round(int(length_ms) / 1000))
                except (TypeError, ValueError):
                    duration_s = None
            recording = track.get("recording") or {}
            rec_mbid = recording.get("id") or None
            out.append({
                "title": title,
                "duration_seconds": duration_s,
                "recording_mbid": rec_mbid,
            })
    return out


async def fetch_recording_duration(
    recording_mbid: str,
    *,
    timeout_s: float = 15.0,
) -> int | None:
    """Fetch the canonical duration (seconds) for a MusicBrainz recording
    entity. Recording MBIDs are stable across reissues — the canonical
    length is populated even when an individual vinyl release's embedded
    track has ``length=None``.

    Returns ``int`` seconds on success. Returns ``None`` when:
      - the recording has no length recorded
      - the HTTP call returns non-200
      - a transport / timeout error fires

    Same User-Agent + aiohttp idioms as ``fetch_release_recordings``.
    """
    if not recording_mbid:
        return None
    url = (
        f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?fmt=json"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from hardcoded musicbrainz.org template; only recording MBID interpolated
                if resp.status != 200:
                    log.warning(
                        "musicbrainz: recording %s returned status=%d",
                        recording_mbid, resp.status,
                    )
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning(
            "musicbrainz: recording %s fetch failed: %r",
            recording_mbid, e,
        )
        return None
    length_ms = data.get("length")
    if length_ms is None:
        return None
    try:
        return int(round(int(length_ms) / 1000))
    except (TypeError, ValueError):
        return None


async def _fetch_caa_front(
    *,
    kind: Literal["release", "release-group"],
    mbid: str,
    size: int,
    timeout_s: float,
) -> tuple[Optional[bytes], Outcome]:
    """Shared CAA front-cover fetcher. ``kind`` selects the URL template
    and the log label; both public wrappers funnel through here so the
    request shape stays exactly one code path."""
    template = CAA_FRONT_URL if kind == "release" else CAA_RG_FRONT_URL
    url = template.format(mbid=mbid, size=size)
    return await _caa_get(url, timeout_s=timeout_s, label=f"{kind}={mbid}")


async def fetch_cover_art(
    mbid: str,
    *,
    size: int = 1200,
    timeout_s: float = 30.0,
) -> tuple[Optional[bytes], Outcome]:
    """Fetch the front cover for a given release MBID from the Cover Art
    Archive.

    Returns `(image_bytes, "ok")` on success, `(None, "missing")` on a
    definitive HTTP 404, or `(None, "transient")` on timeout / connection
    error / 5xx after a 2-attempt retry. The outcome lets the caller
    decide whether stamping the negative cache is safe.
    """
    return await _fetch_caa_front(
        kind="release", mbid=mbid, size=size, timeout_s=timeout_s,
    )


async def fetch_cover_art_release_group(
    rg_mbid: str,
    *,
    size: int = 1200,
    timeout_s: float = 30.0,
) -> tuple[Optional[bytes], Outcome]:
    """Fetch the front cover for a release-*group* MBID. CAA stores RG-level
    art that flows down to all releases in the group — catches anniversary /
    deluxe pressings whose specific release has no art uploaded but whose
    parent group (the original album) does.

    Same return shape as `fetch_cover_art`: `(bytes | None, outcome)`.
    """
    kwargs = {"kind": "release-group", "mbid": rg_mbid,
              "size": size, "timeout_s": timeout_s}
    return await _fetch_caa_front(**kwargs)


async def _caa_get(
    url: str, *, timeout_s: float, label: str,
) -> tuple[Optional[bytes], Outcome]:
    """GET an image from CAA with a 2-attempt transient retry.

    Outcomes:
      - ("ok", bytes)        — 200 with body
      - (None, "missing")    — definitive 404
      - (None, "transient")  — 5xx / connection error / timeout after 2 attempts

    CAA returns 30x redirects to archive.org for actual image bytes;
    aiohttp follows redirects by default, so the 200 we see is the final
    archive.org response.
    """
    headers = {"User-Agent": USER_AGENT}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in (1, 2):
            try:  # skylos: ignore SKY-L004 — retry-loop body is one logical unit; extracting would obscure attempt-1/attempt-2 differences
                async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from hardcoded coverartarchive.org / musicbrainz.org templates; only MBIDs interpolated
                    if resp.status == 200:
                        return await resp.read(), "ok"
                    if resp.status == 404:
                        # Definitive — don't retry.
                        return None, "missing"
                    # 5xx or other non-success — retry once.
                    if attempt == 1:
                        log.info(
                            "coverart %s status=%d, retrying in 2s",
                            label, resp.status,
                        )
                        await asyncio.sleep(2)
                        continue
                    log.warning(
                        "coverart fetch failed for %s: status=%d",
                        label, resp.status,
                    )
                    return None, "transient"
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 1:
                    log.info(
                        "coverart %s retrying in 2s after %r", label, e,
                    )
                    await asyncio.sleep(2)
                    continue
                log.warning("coverart fetch failed for %s: %r", label, e)
                return None, "transient"
    return None, "transient"
