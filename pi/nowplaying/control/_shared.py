"""Shared helpers across the control endpoint families."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from nowplaying import art_cache
from nowplaying.orchestrator.pin import compute_pin_duration

log = logging.getLogger("nowplaying.control")

# Maximum age (seconds) of an audible-edge entry that's still considered
# usable for estimating the initial track-position at pin time. Matches
# the prune window in `_record_audible_edge_for_llm`. Beyond this we fall
# back to other signals.
_AUDIBLE_EDGE_MAX_AGE_S = 60.0

# Max age (seconds) of `last_unmatched_after_match_unix_ts` for the
# predicted-advance latency path. Beyond this, the boundary is stale
# (e.g. user reopened the kiosk hours later) — fall back to 0.0.
_FIRST_MISS_MAX_AGE_S = 300.0


def _last_audible_edge(state) -> dict | None:
    """Return the most recent 'audible' edge entry from state, or None."""
    edges = getattr(state, "recent_audible_edges", None) or []
    for e in reversed(edges):
        if e.get("type") == "audible":
            return e
    return None


def _pin_matches_last_vinyl(state, pin_release_id: int, pin_track_position: str) -> bool:
    """False when last_vinyl records a *different* track than the pin."""
    last_vinyl = getattr(state, "last_vinyl", None)
    if last_vinyl is None:
        return True
    return (
        last_vinyl.get("release_id") == pin_release_id
        and last_vinyl.get("track_position") == pin_track_position
    )


def _pin_matches_anchor(state, pin_release_id: int, pin_track_position: str) -> bool:
    """False when the fingerprint anchor is for a *different* track than the pin."""
    anchor = getattr(state, "fingerprint_anchor", None)
    if anchor is None:
        return True
    return (
        anchor.get("release_id") == pin_release_id
        and anchor.get("track_position") == pin_track_position
    )


def _edges_bound_single_track(state) -> bool:
    """True when exactly one audible-edge exists and no silent-edge since — i.e.
    the audio context is a single uninterrupted track from needle-drop to now."""
    edges = getattr(state, "recent_audible_edges", None) or []
    audible_edges = [e for e in edges if e.get("type") == "audible"]
    silent_edges = [e for e in edges if e.get("type") == "silent"]
    return len(audible_edges) == 1 and not silent_edges


def _no_other_track_seen(state, pin_track_position: str) -> bool:
    """True when session memory contains no recognition for a track other than
    the pin's position since the last audible-edge.

    The snapshot-only checks (_pin_matches_last_vinyl, _pin_matches_anchor)
    always pass at gate time because `_apply_pin_to_locked` has overwritten
    `last_vinyl["track_position"]` to the pin's position by then; this check
    guards that path via session memory.
    """
    seen = getattr(state, "tracks_seen_since_audible_edge", None) or set()
    return not any(pos != pin_track_position for pos in seen)


def _is_fresh_side_first_track_for_pin(
    state, pin_release_id: int, pin_track_position: str,
) -> bool:
    """True when the audio context bounds a single track that matches
    the pin — the safe condition for retroactive backfill.

    A vinyl side always begins with silence before the first track, so
    the first audible-edge after a long silence is bounded by that
    silence. If no silent edge has fired since (no detected
    track-boundary) and no prior recognition of a *different* track has
    occurred, the audio between audible-edge and now is unambiguously
    the pinned track.

    Tracks 2+ on a side fail this gate (last_vinyl was set by the
    prior track's recognition; OR a silent→audible cycle is present
    in recent_audible_edges) and route to forward-only capture.
    """
    return (
        _pin_matches_last_vinyl(state, pin_release_id, pin_track_position)
        and _pin_matches_anchor(state, pin_release_id, pin_track_position)
        and _edges_bound_single_track(state)
        and _no_other_track_seen(state, pin_track_position)
    )


def _first_miss_initial_position_s(state) -> float | None:
    """Return seconds elapsed since the first Shazam-miss after the most
    recent confirm, or None if unusable.

    This is the predicted-advance latency: when the user pins a track
    via the inline confirm card, the real track has been playing since
    roughly that first-miss timestamp (the prior track stopped being
    recognized then), not since pin time. Returns None when the field
    is unset, in the past beyond the max-age window, or in the future.

    See docs/features/pin-position-ignores-predicted-advance-latency/.
    """
    first_miss_ts = getattr(state, "last_unmatched_after_match_unix_ts", None)
    if first_miss_ts is None:
        return None
    elapsed = time.time() - float(first_miss_ts)
    if elapsed < 0.0 or elapsed > _FIRST_MISS_MAX_AGE_S:
        return None
    return elapsed


def _anchor_position_s(
    state, release_id: int, track_position: str, now_mono: float,
) -> float | None:
    """Return extrapolated position from a matching fingerprint anchor, or None."""
    anchor = getattr(state, "fingerprint_anchor", None)
    if anchor is None:
        return None
    if anchor.get("release_id") != release_id or anchor.get("track_position") != track_position:
        return None
    last_pos = anchor.get("last_matched_ref_position_s")
    anchor_mono = anchor.get("monotonic_ts")
    if last_pos is None or anchor_mono is None:
        return None
    return float(last_pos) + (now_mono - float(anchor_mono))


def _audible_edge_position_s(
    state, release_id: int, track_position: str, now_mono: float,
) -> float | None:
    """Return elapsed-since-audible-edge position when conditions are met, or None."""
    if not _is_fresh_side_first_track_for_pin(state, release_id, track_position):
        return None
    edge = _last_audible_edge(state)
    if edge is None:
        return None
    edge_mono = edge.get("_ts_mono")
    if edge_mono is None:
        return None
    age = now_mono - float(edge_mono)
    if 0.0 <= age < _AUDIBLE_EDGE_MAX_AGE_S:
        return float(age)
    return None


def _estimate_initial_track_position_s(
    state,
    release_id: int,
    track_position: str,
    now_mono: float,
) -> float:
    """Estimate where in the track the user is at pin time, in seconds.

    Used by pin-driven coverage promotion to tag fingerprint refs at
    correct positions within the track audio.

    Estimation order:
    1. Fingerprint anchor on the SAME track being pinned: extrapolate
       from the anchor's last matched ref position. Most authoritative
       since the position comes from real fp_refs data.
    2. Fresh-side-first-track gate passes (single audible-edge, no
       silent edge since, no different-track recognition): use the
       audible-edge backdate. Safe because audio context contains
       only this one track.
    3. Cold-start fallback: 0.0. Treats click as track-start. Pin
       coverage proceeds forward from click time.
    """
    anchor_pos = _anchor_position_s(state, release_id, track_position, now_mono)
    if anchor_pos is not None:
        return anchor_pos
    edge_pos = _audible_edge_position_s(state, release_id, track_position, now_mono)
    if edge_pos is not None:
        return edge_pos
    first_miss = _first_miss_initial_position_s(state)
    if first_miss is not None:
        return first_miss
    return 0.0


def _audible_edge_unix_ts(state) -> int | None:
    """Return the unix timestamp of the most recent audible-edge, or None.

    Used to match clip filenames (which are unix-timestamp prefixed) for
    retroactive backfill promotion.
    """
    edge = _last_audible_edge(state)
    if edge is None:
        return None
    ts_iso = edge.get("ts_iso")
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _safe_art_fetch(release_id: int, artist: str, album: str) -> None:
    """Fire-and-forget wrapper around ``art_cache.maybe_cache``.

    Lets ``identify_clip`` schedule the MusicBrainz Cover Art Archive lookup
    via ``asyncio.create_task`` without risking an unhandled-exception
    warning if the upstream call blows up in some unexpected way. The
    helper itself already swallows the expected error classes; this is
    the belt-and-suspenders catch-all.
    """
    try:
        await art_cache.maybe_cache(release_id, artist, album)
    except Exception as e:  # noqa: BLE001 — log+swallow is the contract
        log.warning(
            "identify art-fetch failed for release=%s: %r", release_id, e
        )


def _tracklist_from_release(rel: dict) -> list[dict]:
    """Project a discogs_catalog release row into the tracklist shape next_track expects."""
    return [
        {
            "position": t["position"],
            "side": t.get("side") or (t["position"][:1] if t.get("position") else ""),
            "title": t["title"],
            "duration_seconds": t.get("duration_seconds"),
        }
        for t in (rel.get("tracks") or [])
    ]


def _maybe_schedule_art_fetch(rid: int, payload: dict) -> None:
    """Fire MB Cover Art Archive lookup if artist+album are present."""
    art_artist = payload.get("artist") or ""
    art_album = payload.get("album") or ""
    if art_artist and art_album:
        asyncio.create_task(_safe_art_fetch(rid, art_artist, art_album))


def _apply_user_track_pin(
    state,
    rid: int,
    pos: str,
    matched: dict,
    track_started_at_iso: str | None = None,
) -> None:
    """Set ``state.user_track_pin`` for a user-confirmed track identity.

    Shared by ``identify_clip`` (full search flow) and ``pin_track``
    (locked-album fast path).  Keeps the pin shape single-sourced.

    ``track_started_at_iso`` is the ISO-8601 timestamp of when the current
    track started playing (``state.track_started_at``).  When supplied,
    the pin's effective TTL duration is computed from the *remaining* track
    time rather than the full duration — see ``compute_pin_duration``.
    Pass ``None`` (or omit) when the track start time is not known; the
    helper will fall back to a conservative safety-margin default.
    """
    duration = compute_pin_duration(
        matched.get("duration_seconds"),
        track_started_at_iso,
    )
    now_mono = asyncio.get_running_loop().time()
    initial_track_position_s = _estimate_initial_track_position_s(
        state, rid, pos, now_mono,
    )
    state.user_track_pin = {
        "release_id": rid,
        "track_position": pos,
        "monotonic_ts": now_mono,
        "duration_seconds": duration,
        # Where the audio actually is in the track at pin time. Pin-driven
        # coverage promotion adds elapsed-since-pin to this to compute the
        # current ref's track_position_s. Without this, refs get tagged at
        # position 0+elapsed even when the user clicks mid-track, polluting
        # the cohort with mis-labeled positions.
        "initial_track_position_s": initial_track_position_s,
    }
    state.pin_different_track_streak = 0
    # Stamp wall-clock so the next pin's backfill window anchors to this
    # pin (not the older Shazam-confirm) when they're on the same album lock.
    state.last_pin_unix_ts = int(time.time())
    log.info(
        "pin applied: release=%s pos=%s initial_track_position_s=%.1f duration=%s",
        rid, pos, initial_track_position_s, duration,
    )
