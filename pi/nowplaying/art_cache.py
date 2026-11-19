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

Rate-limiting:

- MusicBrainz enforces a strict 1 request-per-second rate limit.
- ``musicbrainz_slot`` is the one gate every MusicBrainz HTTP request in
  the process goes through (``coverart`` and
  ``discovery.musicbrainz_lookup`` take it around each request). It holds
  ``_mb_semaphore`` (Semaphore(1)) for the request and, before letting it
  start, waits until ``MUSICBRAINZ_MIN_INTERVAL_S`` has passed since the
  previous one finished (a semaphore alone only limits concurrency, not
  rate). Cover Art Archive requests are a different service and do not
  take it.
- ``_mb_inflight`` tracks release_ids currently being fetched so that a
  burst of concurrent /art/<id> 404s (e.g. dashboard loading 10 album cards
  at once) only spawns one task per release_id rather than queuing many
  redundant tasks that all drain through the gate sequentially.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from nowplaying import coverart
from nowplaying._io_safe import safe_write_bytes

log = logging.getLogger("nowplaying.art_cache")

REPO_ROOT = Path(__file__).resolve().parents[2]
MUSICBRAINZ_ART_DIR = REPO_ROOT / "pi" / "data" / "art" / "musicbrainz"

# MusicBrainz's anonymous rate limit: one request per second.
MUSICBRAINZ_MIN_INTERVAL_S = 1.0
# Serialises all outbound MusicBrainz HTTP calls — one in-flight at a time.
_mb_semaphore: asyncio.Semaphore | None = None
# time.monotonic() when the previous MusicBrainz request finished.
_mb_last_request_at: float | None = None
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


@contextlib.asynccontextmanager
async def musicbrainz_slot() -> AsyncIterator[None]:
    """Hold the process-wide MusicBrainz slot for one HTTP request.

    One request is in flight at a time, and it starts no sooner than
    ``MUSICBRAINZ_MIN_INTERVAL_S`` after the previous one finished. Wrap
    only the request itself, so the caller's HTTP timeout does not count
    the wait for the slot.
    """
    global _mb_last_request_at
    async with _get_semaphore():
        if _mb_last_request_at is not None:
            wait_s = (
                _mb_last_request_at + MUSICBRAINZ_MIN_INTERVAL_S
                - time.monotonic()
            )
            if wait_s > 0:
                await asyncio.sleep(wait_s)
        try:
            yield
        finally:
            _mb_last_request_at = time.monotonic()


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
    - If the file already exists, return without any HTTP call.
    - The MusicBrainz search inside ``coverart.fetch_release_mbid`` takes
      ``musicbrainz_slot`` itself; the Cover Art Archive downloads that
      follow are not MusicBrainz requests.
    - ``_mb_inflight`` is managed by the *caller* (art_handler) so that it can
      skip scheduling duplicate tasks before they even reach this function.
    """
    if not artist or not album:
        return
    out = MUSICBRAINZ_ART_DIR / f"{release_id}.jpg"
    if out.exists():
        return
    try:
        found = await coverart.fetch_release_mbid(artist, album)
    except coverart.Unreachable as e:
        log.info(
            "[coverart] MusicBrainz unreachable for release=%s: %r",
            release_id, e,
        )
        return
    if not found:
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
