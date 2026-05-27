"""POST /control/next-track — advance to next track on the same side."""
from __future__ import annotations

import asyncio

from aiohttp import web

from nowplaying.discogs import catalog as discogs_catalog

from nowplaying.control._shared import (
    _maybe_schedule_art_fetch,
    _now_iso,
    _tracklist_from_release,
    log,
)
from nowplaying.orchestrator.pin import compute_pin_duration


def _find_position_index(tracklist: list[dict], position: str) -> int | None:
    target = position.strip().upper()
    for i, t in enumerate(tracklist):
        if (t.get("position") or "").strip().upper() == target:
            return i
    return None


def _find_next_on_side(tracklist: list[dict], start_idx: int, side: str) -> dict | None:
    side_uc = side.strip().upper()
    for t in tracklist[start_idx + 1:]:
        t_side = (t.get("side") or "").strip().upper() or (
            (t.get("position") or "")[:1].upper()
        )
        if t_side == side_uc:
            return t
    return None


def _resolve_next_tracklist(state, rid: int) -> tuple[list[dict] | None, web.Response | None]:
    """Return (tracklist, None) or (None, error_response). Prefers payload tracklist, falls back to catalog."""
    tracklist = list(state.last_vinyl.get("tracklist") or [])
    if not tracklist:
        rel = discogs_catalog.get_release(rid)
        if rel is None:
            return None, web.json_response(
                {"ok": False, "error": "release not in local catalog"}, status=404,
            )
        tracklist = _tracklist_from_release(rel)
    if not tracklist:
        return None, web.json_response(
            {"ok": False, "error": "no tracklist available"}, status=404,
        )
    return tracklist, None


def _current_side(tracklist: list[dict], cur_idx: int, prev_pos: str) -> str:
    """Derive side label from tracklist entry, falling back to position prefix."""
    prev_pos_uc = prev_pos.upper()
    return (tracklist[cur_idx].get("side") or "").strip().upper() or (
        prev_pos_uc[:1] if prev_pos_uc else ""
    )


async def next_track(request: web.Request) -> web.Response:
    """User tapped "Next track →" on the kiosk admin overlay.

    Advances ``state.last_vinyl["track_position"]`` to the next track on
    the *same side* without changing the album-lock release. Solves the
    gapless-album case where the recognizer keeps re-confirming the
    locked release and the track position never moves.

    Body: ``{release_id: int, current_track_position: str}``.

    Returns ``{ok: false, reason: "last_track"}`` (HTTP 200) when the
    current track is the last on its side — the kiosk treats that as a
    soft no-op rather than an error.
    """
    state = request.app.get("state")
    bcast = request.app.get("broadcaster")
    if state is None or bcast is None:
        return web.json_response({"ok": False, "error": "orchestrator not ready"}, status=503)
    try:
        body = await request.json()
        rid = int(body["release_id"])
        prev_pos = str(body["current_track_position"]).strip()
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": f"bad request: {e!r}"}, status=400)

    if not state.last_vinyl:
        return web.json_response({"ok": False, "error": "no current playback"}, status=409)

    tracklist, err = _resolve_next_tracklist(state, rid)
    if err is not None:
        return err

    cur_idx = _find_position_index(tracklist, prev_pos)
    if cur_idx is None:
        return web.json_response(
            {"ok": False, "error": f"track {prev_pos} not on release {rid}"}, status=404,
        )

    cur_side = _current_side(tracklist, cur_idx, prev_pos)
    nxt = _find_next_on_side(tracklist, cur_idx, cur_side)
    if nxt is None:
        log.info(
            "next-track: release=%s pos=%s is last on side %s — no-op",
            rid, prev_pos, cur_side,
        )
        return web.json_response({"ok": False, "reason": "last_track"})

    next_pos = nxt.get("position")
    next_title = nxt.get("title")
    now_iso = _now_iso()

    _advance_last_vinyl(state, nxt, now_iso, next_pos, next_title)
    _advance_pin_if_active(state, rid, nxt, next_pos)

    log.info(
        "control: next-track advance from %s to %s on release=%s",
        prev_pos, next_pos, rid,
    )

    await bcast.publish(state.last_vinyl)
    _maybe_schedule_art_fetch(rid, state.last_vinyl)

    return web.json_response({
        "ok": True,
        "advanced_to": next_pos,
        "title": next_title,
    })


def _advance_last_vinyl(
    state, nxt: dict, now_iso: str, next_pos: str | None, next_title: str | None,
) -> None:
    """Mutate state.last_vinyl in place to reflect the advanced track."""
    state.last_vinyl["track_position"] = next_pos
    state.last_vinyl["title"] = next_title
    state.last_vinyl["track_started_at"] = now_iso
    state.last_vinyl["match_method"] = "user-identified"
    next_duration = nxt.get("duration_seconds")
    if next_duration is not None:
        state.last_vinyl["duration_seconds"] = next_duration
    else:
        state.last_vinyl.pop("duration_seconds", None)
    # next_track stays on the same side; refresh `side` from the tracklist
    # entry to handle multi-LP / cumulative numbering cases where the
    # position-prefix heuristic in to_now_playing_vinyl is wrong.
    next_side = nxt.get("side")
    if next_side:
        state.last_vinyl["side"] = next_side
    state.track_started_at = now_iso
    state.pending_shazam_only.clear()


def _advance_pin_if_active(state, rid: int, nxt: dict, next_pos: str | None) -> None:
    """If the user pin targets this release, advance it to next_pos.

    The advanced pin is for a track that is just starting (next_track fires
    when the side-flip happens, so elapsed ≈ 0).  Pass ``track_started_at_iso=None``
    so ``compute_pin_duration`` returns the full track duration (fresh-start path).
    """
    if state.user_track_pin is None or state.user_track_pin.get("release_id") != rid:
        return
    state.user_track_pin = {
        "release_id": rid,
        "track_position": next_pos,
        "monotonic_ts": asyncio.get_running_loop().time(),
        "duration_seconds": compute_pin_duration(
            nxt.get("duration_seconds"),
            track_started_at_iso=None,  # fresh track start → full duration
        ),
    }
    state.pin_different_track_streak = 0
    log.info("pin advanced: release=%s pos=%s (via next_track)", rid, next_pos)
