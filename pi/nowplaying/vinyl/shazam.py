"""ShazamIO primary recognizer for vinyl.

We send each heartbeat clip to ShazamIO (free, reverse-engineered Shazam
endpoint). On hit we get artist + title; we reverse-lookup the release in
our Discogs SQLite by fuzzy match. Album/version disambiguation lives in
``discogs.catalog.find_by_artist_title`` (preferred-release stickiness +
side-first bias).

Clips must be <14s — Shazam's backend silently returns empty matches at
>=15s (shazamio Issue #150). Capture defaults to 12s.

The recognize call is wrapped in a process-local circuit breaker
(``nowplaying.vinyl.ratelimit``) that suppresses runaway call rates and
backs off on transient failures. See ``docs/features/shazam-circuit-breaker``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .ratelimit import make_shazam_breaker

logger = logging.getLogger(__name__)

# Module-level singleton. Process-local — orchestrator is single-process.
_breaker = make_shazam_breaker()


def _is_rate_limited(exc: BaseException) -> bool:
    """Best-effort 429 detection: shazamio raises aiohttp errors, but we
    don't want to import aiohttp just for this. Look for a `status`
    attribute (ClientResponseError) or "429" in the message.
    """
    status = getattr(exc, "status", None)
    return status == 429 or "429" in str(exc)


async def identify(wav_path: Path) -> Optional[dict]:
    """Run a Shazam recognition on the clip. Returns track dict or None.

    Includes the best match's `offset` (seconds within the indexed track
    where our clip's audio aligned) and `timeskew` (proportional time-rate
    drift). These let the orchestrator compute a sub-second `track_started_at`
    from `clip_mtime - offset`, which is much more precise than the 12s
    heuristic backdate.

    If the circuit breaker is open, returns ``None`` immediately so the
    cascade falls through to "unmatched" without contacting Shazam.
    """
    try:
        from shazamio import Shazam
    except ImportError:
        raise RuntimeError(
            "shazamio not installed. Add to pyproject.toml shazam extra and uv sync --extra shazam"
        )

    if not _breaker.should_allow():
        logger.debug("shazam: circuit suppressing call to identify(%s)", wav_path)
        return None

    _breaker.record_attempt()
    shazam = Shazam()
    try:
        result = await shazam.recognize(str(wav_path))
    except Exception as exc:  # noqa: BLE001
        _breaker.record_failure(rate_limited=_is_rate_limited(exc))
        raise

    _breaker.record_success()
    track = result.get("track")
    if not track:
        return None
    # Pick the best match (highest hit count if reported; else first). The
    # `matches` array carries the offset/timeskew we need for anchor math.
    matches = result.get("matches") or []
    best_match = matches[0] if matches else {}
    offset = best_match.get("offset")
    timeskew = best_match.get("timeskew")
    return {
        "title": track.get("title"),
        "subtitle": track.get("subtitle"),
        "artist": track.get("subtitle"),
        "isrc": (track.get("isrc") if "isrc" in track else None),
        "album": _extract_album(track),
        "art_url": _extract_art_url(track),
        "albumadamid": track.get("albumadamid"),
        "raw": track,
        "match_offset_s": float(offset) if offset is not None else None,
        "match_timeskew": float(timeskew) if timeskew is not None else None,
    }


def _extract_album(track: dict) -> Optional[str]:
    """Pull the album name out of `track.sections[*].metadata`.

    Shazam returns a list of "sections" (e.g. SONG, ARTIST, VIDEO). The SONG
    section has a `metadata` list of {title, text} pairs; one of those carries
    the album name. Returns None when the album entry is missing.
    """
    for section in track.get("sections") or []:
        for entry in section.get("metadata") or []:
            if entry.get("title") == "Album":
                text = entry.get("text")
                return text if text else None
    return None


def _extract_art_url(track: dict) -> Optional[str]:
    """Prefer the hi-res `coverarthq`; fall back to `coverart`. Returns None
    when no `images` block (or no usable URL) is present."""
    images = track.get("images") or {}
    return images.get("coverarthq") or images.get("coverart") or None


