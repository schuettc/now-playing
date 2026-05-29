"""Last.fm scrobble pipeline for confirmed plays."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from .. import scrobble as _scrobble

log = logging.getLogger("nowplaying.history")

# Last.fm scrobble policy: only scrobble tracks ≥ 30s long, and only when the
# listener has heard ≥ 50% OR ≥ 240s. Matches the official Last.fm rule.
SCROBBLE_MIN_DURATION_S = 30
SCROBBLE_MIN_ELAPSED_S = 240

# No per-track duration available (Discogs blank + MusicBrainz unmatched):
# can't compute the 50% leg, and requiring 240s makes sub-4-minute tracks
# impossible to scrobble. Heartbeats are silence-gated, so `elapsed` is real
# audible playtime — 120s is a substantial, genuine listen (4x the Last.fm
# 30s floor) and well within the client-convention's intent.
SCROBBLE_UNKNOWN_DURATION_MIN_S = 120

# Track which play_history row ids have already been scrobbled so coalesced
# heartbeats don't double-submit. Bounded FIFO eviction to avoid unbounded
# growth on long-running orchestrators.
_scrobbled_ids: dict[int, None] = {}
_SCROBBLE_CACHE_LIMIT = 500

# Track which (row id) have already had a now-playing notification sent so
# repeated heartbeats for the same play only fire one "now playing" ping.
_now_playing_ids: dict[int, None] = {}
_NOW_PLAYING_CACHE_LIMIT = 500

# Lazily-created aiohttp ClientSession shared across scrobble calls. Created
# on first use; closed only at process exit (we never explicitly close it —
# aiohttp will warn on shutdown but the connector is harmless on exit).
_http_session: aiohttp.ClientSession | None = None


def _remember(cache: dict[int, None], limit: int, key: int) -> None:
    cache[key] = None
    while len(cache) > limit:
        # Pop the oldest (insertion-order is preserved on dict since 3.7).
        try:
            cache.pop(next(iter(cache)))
        except StopIteration:
            break


async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


@dataclass(slots=True)
class _ScrobbleCtx:
    """Bundle of fields needed to dispatch a scrobble. Replaces a 6/7-arg
    parameter list on the helpers below."""
    row_info: dict
    row_id: int
    artist: str
    title: str
    album: str | None
    session: aiohttp.ClientSession


def _scrobble_fields(row_info: dict) -> tuple[str, str, str | None, int | None]:
    """Extract (artist, title, album, row_id) from a row_info dict, normalizing
    blanks to empty strings and album to None when missing."""
    artist = (row_info.get("artist") or "").strip()
    title = (row_info.get("title") or "").strip()
    album = row_info.get("album") or None
    row_id = row_info.get("id")
    return artist, title, album, row_id


def _should_scrobble(elapsed: int, duration: int) -> bool:
    """Last.fm scrobble eligibility: track ≥30s long AND listener heard ≥50% OR ≥240s.

    When ``duration`` is unknown (Discogs catalog row without per-track
    duration, and MusicBrainz enrichment hasn't run on it yet), fall back
    to the ≥120s elapsed leg — heartbeats are silence-gated so ``elapsed``
    is real audible playtime; 120s is 4× the Last.fm 30s floor and within
    the client-convention's intent without requiring the impossible 240s
    threshold for sub-4-minute tracks.
    """
    if duration <= 0:
        return elapsed >= SCROBBLE_UNKNOWN_DURATION_MIN_S
    if duration < SCROBBLE_MIN_DURATION_S:
        return False
    if elapsed < SCROBBLE_MIN_ELAPSED_S and elapsed < (duration // 2):
        return False
    return True


async def _maybe_fire_now_playing(ctx: _ScrobbleCtx) -> None:
    if not ctx.row_info.get("inserted") or ctx.row_id in _now_playing_ids:
        return
    _remember(_now_playing_ids, _NOW_PLAYING_CACHE_LIMIT, ctx.row_id)
    try:
        await _scrobble.update_now_playing(
            ctx.artist, ctx.title, ctx.album, session=ctx.session,
        )
    except Exception as e:  # pragma: no cover — defensive
        log.warning("update_now_playing crashed: %r", e)


async def _maybe_scrobble(ctx: _ScrobbleCtx, duration_seconds: int | None) -> None:
    if ctx.row_id in _scrobbled_ids:
        return
    elapsed = int(ctx.row_info["ended_at"]) - int(ctx.row_info["started_at"])
    duration = int(duration_seconds or 0)
    if not _should_scrobble(elapsed, duration):
        return
    _remember(_scrobbled_ids, _SCROBBLE_CACHE_LIMIT, ctx.row_id)
    try:
        await _scrobble.scrobble(
            ctx.artist, ctx.title, ctx.album,
            int(ctx.row_info["started_at"]),
            session=ctx.session,
        )
    except Exception as e:  # pragma: no cover — defensive
        log.warning("scrobble crashed: %r", e)


async def _safe_scrobble(row_info: dict, duration_seconds: int | None) -> None:
    """Fire Last.fm now-playing (on fresh insert) and scrobble (when the
    row meets the Last.fm threshold). Never raises; wraps every step in
    broad exception handling so a Last.fm outage can't bring down the
    orchestrator."""
    try:
        artist, title, album, row_id = _scrobble_fields(row_info)
        if not artist or not title or row_id is None:
            return
        session = await _get_http_session()
        ctx = _ScrobbleCtx(row_info, row_id, artist, title, album, session)
        await _maybe_fire_now_playing(ctx)
        await _maybe_scrobble(ctx, duration_seconds)
    except Exception as e:  # pragma: no cover — defensive
        log.warning("_safe_scrobble crashed: %r", e)
