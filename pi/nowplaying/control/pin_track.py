"""POST /api/pin-track — pin a track on the currently-locked album (fast path)."""
from __future__ import annotations

import asyncio
import dataclasses
import time

from aiohttp import web

from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.vinyl import promotion

from nowplaying.control._shared import (
    _apply_user_track_pin,
    _audible_edge_unix_ts,
    _first_miss_initial_position_s,
    _is_fresh_side_first_track_for_pin,
    _maybe_schedule_art_fetch,
    _now_iso,
    _tracklist_from_release,
    log,
)


def _schedule_pin_backfill(
    state, release_id: int, track_position: str, duration_s: float | None,
    prior_track_position: str | None = None,
    prior_pin_unix_ts: int | None = None,
    prior_pin_duration_seconds: float | int | None = None,
) -> None:
    """Fire retroactive coverage promotion for clips that pre-date this pin.

    Two boundary sources, tried in order:

    1. Fresh-side-first-track: audible-edge is a reliable bound because
       a vinyl side begins with silence and we've seen exactly one
       audible edge since. Works only for the first track of a side.
    2. Predicted-transition: when the pin transitions to a track that
       differs from `prior_track_position` AND we have a recent
       Shazam-confirmed match (`state.last_shazam_match_unix_ts`), use
       that timestamp as the lower bound. Handles the mid-side case
       where predicted-advance fired after 2 Shazam misses and the
       user then pinned the predicted track.

    Both paths flow through `promotion.schedule_backfill_promotions`,
    which applies the cross-cohort guard per clip — so a stale boundary
    or wrong pin can't poison the cohort.
    """
    pin_ts = int(time.time())
    edge_ts: int | None = None
    if _is_fresh_side_first_track_for_pin(state, release_id, track_position):
        edge_ts = _audible_edge_unix_ts(state)
    if edge_ts is None:
        edge_ts = _predicted_transition_boundary(
            state, track_position, prior_track_position,
            prior_pin_unix_ts=prior_pin_unix_ts,
        )
    if edge_ts is None:
        log.info(
            "pin-backfill: no usable boundary; skipping (release=%s pos=%s)",
            release_id, track_position,
        )
        return
    # Tighten the window when the boundary derives from a prior pin: the
    # prior track plays AFTER its pin timestamp, so clips in
    # [prior_pin_ts, prior_pin_ts + prior_pin_duration_seconds] are
    # actually the PRIOR track's audio. Skip them by advancing the lower
    # bound to the prior track's expected end. Audible-edge path is
    # untouched (no prior pin → no tightening). When prior duration is
    # unknown (no catalog data), behavior matches today.
    # See docs/features/backfill-window-assumes-boundary-is-track-start/.
    if (
        prior_pin_unix_ts is not None
        and prior_pin_duration_seconds is not None
    ):
        prior_track_end = int(prior_pin_unix_ts) + int(prior_pin_duration_seconds)
        if prior_track_end > edge_ts:
            edge_ts = prior_track_end
    asyncio.create_task(
        promotion.schedule_backfill_promotions(
            release_id=release_id,
            track_position=track_position,
            audible_edge_unix_ts=edge_ts,
            pin_unix_ts=pin_ts,
            duration_s=duration_s,
        ),
    )


def _predicted_transition_boundary(
    state, track_position: str, prior_track_position: str | None,
    *,
    prior_pin_unix_ts: int | None = None,
) -> int | None:
    """Return the boundary unix ts when the pin represents a mid-side
    transition that warrants backfill, else None.

    The boundary is `max(last_shazam_match_unix_ts, prior_pin_unix_ts)`,
    ignoring Nones. This handles chained pins on the same album lock:
    after pinning B6 the prior-pin ts becomes the floor for B7's backfill
    window, so the window can't sweep up B6's audio when the last
    Shazam-confirm predates B6.

    `prior_pin_unix_ts` is the value of `state.last_pin_unix_ts` as
    captured BEFORE the current pin's `_apply_user_track_pin` stamp
    clobbered it. Reading `state.last_pin_unix_ts` directly would yield
    `pin_ts` for every pin → degenerate `[pin_ts, pin_ts]` window.
    See docs/features/pin-backfill-boundary-clobbered-by-self/.

    Gates:
      - pin's track_position differs from prior_track_position
      - at least one of (last_shazam_match_unix_ts, prior_pin_unix_ts) is set

    No max-age cap on the boundary: idle cleanup clears both timestamps
    after ~2 min of silence, and the cross-cohort guard at clip-write
    time catches mis-labeled audio. See
    docs/features/remove-predicted-transition-max-age/.
    """
    if prior_track_position is None or prior_track_position == track_position:
        return None
    last_shazam = getattr(state, "last_shazam_match_unix_ts", None)
    candidates = [int(t) for t in (last_shazam, prior_pin_unix_ts) if t is not None]
    if not candidates:
        return None
    return max(candidates)


def _bad_pin_request(reason: str, error: str) -> web.Response:
    """4xx response with machine-readable `reason` code + human message."""
    return web.json_response(
        {"ok": False, "reason": reason, "error": error}, status=400,
    )


def _find_pin_track(tracklist: list[dict], target: str) -> dict | None:
    """Find the track matching `target` position in `tracklist`,
    case/whitespace insensitive. Returns the track dict or None."""
    norm = target.strip().upper()
    for t in tracklist or []:
        if (t.get("position") or "").strip().upper() == norm:
            return t
    return None


def _resolve_pin_tracklist(state, rid: int, pos: str) -> list[dict] | None:
    """Resolve the tracklist for a locked-album pin.

    Prefers ``state.last_vinyl["tracklist"]`` when it contains the requested
    position (fast path, no DB round-trip). Falls back to
    ``discogs_catalog.get_release(rid)`` when the inline tracklist is absent
    OR when it is non-empty but doesn't contain ``pos`` — the latter covers
    the predicted-advance + blind-discovery case where the inline list holds
    only the currently-active track, not the full release.

    Returns None when both sources are empty/unavailable.
    """
    locked = state.last_vinyl
    inline = (locked or {}).get("tracklist") or []
    if inline and _find_pin_track(inline, pos):
        return inline
    rel = discogs_catalog.get_release(rid)
    if rel is None:
        return inline or None
    return _tracklist_from_release(rel)


def _pin_ttl_seconds(
    duration_seconds: int | None,
    track_started_at_iso: str | None,
) -> int | None:
    """Compute the pin TTL for the API response (remaining time at click).

    Delegates to ``compute_pin_duration`` so the response value matches
    exactly what the orchestrator will enforce via ``_pin_ttl_expired``.
    Returns ``None`` when ``duration_seconds`` is unknown.
    """
    from nowplaying.orchestrator.pin import compute_pin_duration
    return compute_pin_duration(duration_seconds, track_started_at_iso)


def _parse_pin_request_body(body: dict) -> tuple[int, str]:
    """Extract (release_id, track_position) from a pin-track request body.
    Raises ValueError/KeyError/TypeError on missing or malformed fields;
    the caller maps to a 400 `bad-request` response."""
    return int(body["release_id"]), str(body["track_position"])


@dataclasses.dataclass(slots=True)
class _PinStateResult:
    """Values produced by :func:`_apply_pin_state` consumed by ``pin_track``."""
    canonical_pos: str
    title: str | None
    duration: int | None
    pin_track_started_at: str | None
    prior_pos: str | None
    prior_pin_unix_ts: int | None
    prior_pin_duration_seconds: float | int | None


def _apply_pin_state(
    state, locked: dict, rid: int, matched: dict, pos: str,
) -> _PinStateResult:
    """Compute timestamps, update orchestrator state, apply user-track-pin.

    Captures all pre-mutation priors, calls :func:`_apply_pin_to_locked` and
    :func:`_apply_user_track_pin`, resolves ``track_started_at`` (with
    predicted-advance backdate when applicable), clears ``predicted_position``,
    and refreshes the confidence stamp for fresh-start pins.

    Returns a :class:`_PinStateResult` with all values the caller needs to
    schedule backfill, log, and build the response.
    """
    now_iso = _now_iso()
    prior_pos = (locked or {}).get("track_position")
    is_different_track = prior_pos != pos
    prior_track_started_at = (
        None if is_different_track else state.track_started_at
    )
    canonical_pos, title, duration = _apply_pin_to_locked(
        locked, matched, pos, now_iso,
    )
    first_miss_offset = (
        _first_miss_initial_position_s(state) if is_different_track else None
    )
    if first_miss_offset is not None:
        state.track_started_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - first_miss_offset),
        )
        # Backdated value is also the right input to pin TTL math: pin
        # represents a track that has already been playing for ~first_miss
        # seconds, so TTL must subtract that elapsed time or the pin
        # outlives the real track end.
        # See docs/features/pin-ttl-ignores-initial-track-position/.
        pin_track_started_at = state.track_started_at
    else:
        state.track_started_at = now_iso
        pin_track_started_at = prior_track_started_at
    state.predicted_position = None
    if is_different_track:
        # Fresh-start: this track just began per the user. Refresh the
        # confidence stamp so state-decay does not fire on the prior
        # track's stale age the moment the pin TTL expires.
        state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
    # Capture `state.last_pin_unix_ts` BEFORE `_apply_user_track_pin`
    # clobbers it with the current pin's wall-clock. The backfill
    # scheduler needs the PRIOR pin's timestamp (or None for the first
    # pin in a session) to compute the correct window lower bound.
    # See docs/features/pin-backfill-boundary-clobbered-by-self/.
    prior_pin_unix_ts = getattr(state, "last_pin_unix_ts", None)
    # Capture the prior pin's duration BEFORE `_apply_user_track_pin`
    # clobbers `state.user_track_pin` with the new pin's payload. Backfill
    # uses this to advance the window lower bound past the prior track's
    # expected end — clips before that end contain prior-track audio.
    # See docs/features/backfill-window-assumes-boundary-is-track-start/.
    prior_pin = getattr(state, "user_track_pin", None)
    prior_pin_duration_seconds = (
        prior_pin.get("duration_seconds") if isinstance(prior_pin, dict) else None
    )
    _apply_user_track_pin(
        state, rid, canonical_pos, matched,
        track_started_at_iso=pin_track_started_at,
    )
    return _PinStateResult(
        canonical_pos=canonical_pos,
        title=title,
        duration=duration,
        pin_track_started_at=pin_track_started_at,
        prior_pos=prior_pos,
        prior_pin_unix_ts=prior_pin_unix_ts,
        prior_pin_duration_seconds=prior_pin_duration_seconds,
    )


def _validate_pin_lock(state, rid: int) -> web.Response | None:
    """Validate that `rid` matches the currently-locked album. Returns a
    4xx response on missing/mismatched lock, None when proceeding."""
    locked = state.last_vinyl
    if locked is None or locked.get("release_id") is None:
        return _bad_pin_request("no-album-locked", "no album currently locked")
    locked_rid = locked.get("release_id")
    if int(locked_rid) != rid:
        return _bad_pin_request(
            "release-id-mismatch",
            f"locked album is {locked_rid}, requested {rid}",
        )
    return None


def _resolve_pin_match(
    state, rid: int, pos: str,
) -> tuple[dict | None, web.Response | None]:
    """Resolve the matched track for a pin request. Returns (matched, None)
    on success, or (None, 4xx response) when tracklist or position can't
    be resolved."""
    tracklist = _resolve_pin_tracklist(state, rid, pos)
    if tracklist is None:
        return None, _bad_pin_request(
            "position-not-in-tracklist", "no tracklist available for release",
        )
    matched = _find_pin_track(tracklist, pos)
    if matched is None:
        return None, _bad_pin_request(
            "position-not-in-tracklist",
            f"track {pos} not found on locked release",
        )
    return matched, None


def _apply_pin_to_locked(
    locked: dict, matched: dict, pos: str, now_iso: str,
) -> tuple[str, str | None, int | None]:
    """Overlay a user-pinned track onto the locked-album payload in place.
    Returns (canonical_pos, title, duration_seconds). Identity fields
    (release_id, artist, album, tracklist) untouched; track-level fields
    overlaid; match metadata bumped."""
    canonical_pos = (matched.get("position") or pos).strip().upper()
    title = matched.get("title")
    duration = matched.get("duration_seconds")
    side = matched.get("side")
    locked["track_position"] = canonical_pos
    locked["title"] = title
    if side is not None:
        locked["side"] = side
    if duration is not None:
        locked["duration_seconds"] = duration
    else:
        locked.pop("duration_seconds", None)
    locked["match_method"] = "user-identified"
    locked["match_confidence"] = "user"
    locked["ts"] = now_iso
    return canonical_pos, title, duration


async def pin_track(request: web.Request) -> web.Response:
    """POST /api/pin-track — pin a track on the currently-locked album.

    Body: `{release_id: int, track_position: str}`. The `release_id`
    MUST match `state.last_vinyl.release_id` (defends against
    album-lock-flipped-mid-tap races). Position is case/whitespace
    tolerant.

    Returns 200 with the canonical pinned payload
    (release_id, track_position, title, duration_seconds,
    pin_ttl_seconds). Returns 400 with machine-readable `reason`
    on bad input (no-album-locked, release-id-mismatch,
    position-not-in-tracklist, bad-request).

    Side effects: sets `state.user_track_pin`, mutates
    `state.last_vinyl` in place to reflect the tapped track,
    publishes the canonical payload via the broadcaster.

    See `docs/features/tracklist-click-to-identify/`.
    """
    state = request.app.get("state")
    bcast = request.app.get("broadcaster")
    if state is None or bcast is None:
        return web.json_response(
            {"ok": False, "error": "orchestrator not ready"}, status=503,
        )
    try:
        body = await request.json()
        rid, pos = _parse_pin_request_body(body)
    except (ValueError, KeyError, TypeError) as e:
        return _bad_pin_request("bad-request", f"bad request: {e!r}")

    lock_err = _validate_pin_lock(state, rid)
    if lock_err is not None:
        return lock_err

    matched, match_err = _resolve_pin_match(state, rid, pos)
    if match_err is not None:
        return match_err

    locked = state.last_vinyl
    ps = _apply_pin_state(state, locked, rid, matched, pos)
    _schedule_pin_backfill(
        state, rid, ps.canonical_pos, ps.duration,
        prior_track_position=ps.prior_pos,
        prior_pin_unix_ts=ps.prior_pin_unix_ts,
        prior_pin_duration_seconds=ps.prior_pin_duration_seconds,
    )
    log.info(
        "pin-track: release=%s pos=%s title=%r duration=%s "
        "(pin set with remaining-time TTL)",
        rid, ps.canonical_pos, ps.title, ps.duration,
    )
    await bcast.publish(locked)
    _maybe_schedule_art_fetch(rid, locked)

    return web.json_response({
        "ok": True,
        "release_id": rid,
        "track_position": ps.canonical_pos,
        "title": ps.title,
        "duration_seconds": ps.duration,
        "pin_ttl_seconds": _pin_ttl_seconds(
            ps.duration,
            ps.pin_track_started_at,
        ),
    })
