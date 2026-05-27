"""POST /control/clear-fingerprints — delete learned fp_refs for one track.

User-driven undo for the kiosk's confirm-and-learn flow. When a tap (or
the LLM reverse-lookup judge) promoted the wrong release/track, those
fingerprints stick around forever and keep mis-attributing the audio.
This endpoint nukes the cohort so the cascade re-learns on the next
confirmed match.

Scope: one ``(release_id, track_position)`` at a time. Bulk-clear and
time-bounded clear are explicitly out of scope (see idea.md).
"""
from __future__ import annotations

from aiohttp import web

from nowplaying.control._shared import log
from nowplaying.vinyl import fingerprint


async def clear_fingerprints(request: web.Request) -> web.Response:
    """Delete fp_refs for one (release_id, track_position).

    Body: ``{"release_id": int, "track_position": str}``
    Response: ``{"ok": true, "cleared": int}`` on success;
    ``{"ok": false, "error": "..."}`` with 400 on missing/bad fields.

    The orchestrator does NOT auto-replenish on the next heartbeat —
    the user-driven learning flow kicks in naturally when the next
    confirmed match lands. See docs/features/clear-learned-fingerprints/.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    release_id = body.get("release_id")
    track_position = body.get("track_position")
    if not isinstance(release_id, int) or not isinstance(track_position, str):
        return web.json_response(
            {"ok": False, "error": "release_id (int) and track_position (str) required"},
            status=400,
        )
    if not track_position:
        return web.json_response(
            {"ok": False, "error": "track_position must be non-empty"},
            status=400,
        )
    db_path = request.app.get("fp_db_path") or fingerprint.DEFAULT_DB_PATH
    cleared = fingerprint.delete_refs_for_track(
        release_id=release_id,
        track_position=track_position,
        db_path=db_path,
    )
    log.info(
        "clear_fingerprints: release=%s pos=%s cleared=%d",
        release_id, track_position, cleared,
    )
    return web.json_response({"ok": True, "cleared": cleared})
