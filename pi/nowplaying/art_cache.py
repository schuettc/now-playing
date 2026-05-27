"""Shared helper: fetch canonical album art from MusicBrainz Cover Art
Archive and cache locally under ``pi/data/art/musicbrainz/<release_id>.jpg``.

Extracted from ``pi/scripts/recognize_proto.py`` so both the recognition
cascade (a script) and the control endpoints (the ``nowplaying`` package)
can trigger it without crossing the script/package boundary or risking a
circular import.

Free, no API key. Falls back to the Discogs scan when no Cover Art Archive
match is found. No-op when art is already cached. Errors are logged to
stderr — never raises.

Negative-cache semantics (from PR #74 / art-pipeline-reliability):

- We only stamp the negative cache when BOTH the release-level and
  release-group-level CAA calls returned definitive HTTP 404 ("missing").
- A transient miss (timeout, 5xx, connection error) on either step
  short-circuits the negative stamp, so the next recognition retries
  cleanly.

Rate-limiting (added for stats-missing-album-art, PR #154):

- MusicBrainz enforces a strict 1 request-per-second rate limit.
- ``_mb_semaphore`` (Semaphore(1)) serializes all in-flight HTTP calls to
  MusicBrainz across the whole process. A 1-second sleep *after* each
  successful HTTP call enforces the inter-request delay (a semaphore alone
  only limits concurrency, not rate).
- ``_mb_inflight`` tracks release_ids currently being fetched so that a
  burst of concurrent /art/<id> 404s (e.g. dashboard loading 10 album cards
  at once) only spawns one task per release_id rather than queuing many
  redundant tasks that all drain through the semaphore sequentially.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nowplaying import coverart
from nowplaying._io_safe import safe_write_bytes

log = logging.getLogger("nowplaying.art_cache")

REPO_ROOT = Path(__file__).resolve().parents[2]
MUSICBRAINZ_ART_DIR = REPO_ROOT / "pi" / "data" / "art" / "musicbrainz"

# Serialises all outbound MusicBrainz HTTP calls — one in-flight at a time.
# The sleep(1) inside the critical section enforces the 1 req/s rate limit.
_mb_semaphore: asyncio.Semaphore | None = None
# Set of release_ids currently being fetched. Prevents duplicate tasks for
# the same release when the dashboard issues several concurrent /art/<id>
# requests before the first one completes.
_mb_inflight: set[int] = set()


def _get_semaphore() -> asyncio.Semaphore:
    """Return (and lazily create) the module-level semaphore.

    Lazy creation avoids an ``asyncio.Semaphore()`` call at import time,
    which would fail if no event loop is running (e.g. in unit tests that
    import the module without starting asyncio).
    """
    global _mb_semaphore
    if _mb_semaphore is None:
        _mb_semaphore = asyncio.Semaphore(1)
    return _mb_semaphore


def _handle_missing_art(
    release_id: int,
    artist: str,
    album: str,
    outcome_release: str,
    outcome_rg: str,
) -> None:
    """Stamp the negative cache only when both upstream calls returned
    definitive HTTP 404 ("missing"). A transient (timeout / 5xx /
    connection error) on either step must NOT poison the cache — the
    next recognition will retry cleanly. This is the core fix for
    art-pipeline-reliability: prevent CAA timeouts on slow days from
    permanently blocking otherwise-fetchable releases.
    """
    if outcome_release == "missing" and outcome_rg == "missing":
        coverart.mark_negative(artist, album)
        return
    log.info(
        "[coverart] transient miss for release=%s (%s/%s); not stamping negative",
        release_id,
        outcome_release,
        outcome_rg or "n/a",
    )


def _write_art(out: Path, data: bytes, release_id: int, source: str) -> None:
    try:
        MUSICBRAINZ_ART_DIR.mkdir(parents=True, exist_ok=True)
        safe_write_bytes(out, data)
        log.info(
            "[coverart] cached musicbrainz art for release=%s %s",
            release_id,
            source,
        )
    except OSError as e:
        log.warning("[coverart] write failed: %r", e)


async def maybe_cache(release_id: int, artist: str, album: str) -> None:
    """Background task: cache MusicBrainz cover art for ``release_id``.

    Same signature, behavior, and logging as the prior in-script helper
    ``_maybe_cache_musicbrainz_art``. Safe to call as
    ``asyncio.create_task(art_cache.maybe_cache(...))``.

    Concurrency / rate-limiting:
    - Fast pre-check: if the file already exists, return immediately without
      touching the semaphore.
    - Acquire ``_mb_semaphore`` (Semaphore(1)) before any outbound HTTP call.
      This serializes all MusicBrainz requests process-wide.
    - TOCTOU double-check: re-test file existence inside the semaphore so a
      queued task skips the MB call if a prior task already wrote the file.
    - After a successful HTTP round-trip, sleep 1 second inside the semaphore
      to enforce MusicBrainz's 1 req/s rate limit. (Semaphore(1) alone limits
      concurrency but not rate — without the sleep, sub-second responses would
      allow back-to-back requests faster than 1/s.)
    - ``_mb_inflight`` is managed by the *caller* (art_handler) so that it can
      skip scheduling duplicate tasks before they even reach this function.
    """
    if not artist or not album:
        return
    out = MUSICBRAINZ_ART_DIR / f"{release_id}.jpg"
    # Fast pre-check: skip semaphore acquisition when art is already on disk.
    if out.exists():
        return
    sem = _get_semaphore()
    async with sem:
        # TOCTOU double-check: another task may have written the file while
        # we waited to acquire the semaphore.
        if out.exists():
            return
        found = await coverart.fetch_release_mbid(artist, album)
        if not found:
            # No MB match — release the semaphore promptly; still sleep to
            # avoid hammering the MBID lookup endpoint on a burst.
            await asyncio.sleep(1)
            return
        release_mbid, rg_mbid = found
        data, outcome_release = await coverart.fetch_cover_art(release_mbid)
        source = f"release={release_mbid}"
        outcome_rg: str = "missing" if not rg_mbid else ""
        if not data and rg_mbid:
            # Anniversary / deluxe / variant pressings often have no art on the
            # specific MB release but DO have it on the parent release-group.
            data, outcome_rg = await coverart.fetch_cover_art_release_group(rg_mbid)
            source = f"release-group={rg_mbid}"
        if not data:
            _handle_missing_art(release_id, artist, album, outcome_release, outcome_rg)
        else:
            _write_art(out, data, release_id, source)
        # Enforce 1 req/s — sleep inside the semaphore so the next waiter
        # doesn't start its HTTP call until at least 1 second after ours.
        await asyncio.sleep(1)
