"""POST /control/mark-wrong — clear vinyl lock and republish unmatched."""
from __future__ import annotations

from aiohttp import web

from nowplaying.control._shared import _now_iso, log


async def mark_wrong(request: web.Request) -> web.Response:
    """Clear state.last_vinyl and republish an unmatched placeholder so the
    kiosk reverts until the next heartbeat re-recognizes.
    """
    state = request.app.get("state")
    bcast = request.app.get("broadcaster")
    if state is None or bcast is None:
        return web.json_response({"ok": False, "error": "orchestrator not ready"}, status=503)

    try:
        body = await request.json()
    except Exception:
        body = {}
    log.info(
        "mark_wrong: release=%s pos=%s (was %s)",
        body.get("release_id"),
        body.get("track_position"),
        state.last_vinyl.get("title") if state.last_vinyl else None,
    )
    state.last_vinyl = None
    state.last_vinyl_confidence_set_at = None
    state.last_shazam_match_unix_ts = None
    state.last_pin_unix_ts = None
    state.last_unmatched_after_match_unix_ts = None
    # User says the album is wrong → any in-flight prediction is based
    # on the wrong release; clear it so the next heartbeat starts clean.
    state.predicted_position = None
    if state.user_track_pin is not None:
        log.info("pin released: reason=mark_wrong")
    state.user_track_pin = None
    state.pin_different_track_streak = 0
    await bcast.publish({
        "ts": _now_iso(),
        "state": "PLAYING",
        "source": "vinyl",
        "match_method": "unmatched",
    })
    return web.json_response({"ok": True})
