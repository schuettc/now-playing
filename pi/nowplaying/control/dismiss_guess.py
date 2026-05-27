"""POST /api/dismiss-guess — user rejected the current best-guess prompt."""
from __future__ import annotations

import asyncio

from aiohttp import web

from nowplaying.control._shared import log


async def dismiss_guess(request: web.Request) -> web.Response:
    """POST /api/dismiss-guess — user rejected the current best-guess
    prompt. Records `(release_id, track_position)` in
    `state.dismissed_guesses` so subsequent heartbeats don't re-emit
    the same guess for `DISMISSED_GUESS_TTL_S` (5 minutes), keyed by
    monotonic time.

    Side-effect: also clears `state.pending_guess` if it matches the
    dismissed (rid, position) so the next published payload reconciles
    other connected clients to the user-rejected state.

    See `docs/features/identify-guess-confirm/`.
    """
    state = request.app.get("state")
    if state is None:
        return web.json_response(
            {"ok": False, "error": "orchestrator not ready"}, status=503,
        )
    try:
        body = await request.json()
        rid = int(body["release_id"])
        pos = str(body["track_position"])
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response(
            {"ok": False, "reason": "bad-request", "error": f"bad request: {e!r}"},
            status=400,
        )

    now_mono = asyncio.get_running_loop().time()
    state.dismissed_guesses[(rid, pos)] = now_mono

    # Clear matching pending guess so the next publish reconciles.
    pending = state.pending_guess
    if pending and pending.get("position") == pos and state.last_vinyl:
        if state.last_vinyl.get("release_id") == rid:
            state.pending_guess = None

    log.info(
        "dismiss-guess: release=%s pos=%s (now %d entries)",
        rid, pos, len(state.dismissed_guesses),
    )
    return web.json_response({
        "ok": True,
        "release_id": rid,
        "track_position": pos,
    })
