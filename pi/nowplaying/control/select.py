"""POST /control/select-release — user picks an alternate release from the kiosk."""
from __future__ import annotations

import asyncio

from aiohttp import web

from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.vinyl.runtime import to_now_playing_vinyl

from nowplaying.control._shared import _now_iso, _tracklist_from_release, log


def _match_track_by_title(rel: dict, title_lc: str) -> tuple[str | None, str | None]:
    """Return (position, title) of the first track whose title matches (case-insensitive)."""
    for t in (rel.get("tracks") or []):
        if (t.get("title") or "").strip().lower() == title_lc:
            return t.get("position"), t.get("title")
    return None, None


def _resolve_select_track(
    rel: dict, prev: dict, body: dict,
) -> tuple[str | None, str | None]:
    """Resolve (position, title) on the chosen release: prefer title match, fall back to body hints."""
    prev_title = (prev.get("title") or "").strip().lower()
    matched_position, matched_title = _match_track_by_title(rel, prev_title)
    if matched_position is None:
        matched_position = body.get("track_position")
        matched_title = body.get("track_title") or prev.get("title")
    return matched_position, matched_title


def _build_select_payload(
    rel: dict, new_rid: int, matched_position: str | None, matched_title: str | None,
) -> dict:
    """Construct the now-playing payload for a user-selected release."""
    synthetic = {
        "ts": _now_iso(),
        "title": matched_title,
        "artist": rel.get("artist"),
        "album": rel.get("title"),
        "year": rel.get("year"),
        "label": rel.get("label"),
        "catno": rel.get("catno"),
        "track_position": matched_position,
        "release_id": new_rid,
        "match_method": "user-selected",
        "match_confidence": "user",
        "tracklist": _tracklist_from_release(rel),
    }
    return to_now_playing_vinyl(synthetic)


def _apply_select_state(state, payload: dict, new_rid: int) -> None:
    """Apply user-selected payload to state and clear any pin/prediction."""
    if state.sonos_source in ("vinyl", "airplay"):
        payload["source"] = state.sonos_source
    state.last_vinyl = payload
    state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
    state.predicted_position = None
    if state.user_track_pin is not None:
        log.info("pin released: reason=select_release new_rid=%s", new_rid)
    state.user_track_pin = None
    state.pin_different_track_streak = 0


async def select_release(request: web.Request) -> web.Response:
    """User picked an alternate release from the kiosk overlay.
    Rebuilds the now-playing payload from the chosen release_id (looking up
    the matched track via the previously-stored last_vinyl.track_position),
    updates state.last_vinyl, and re-publishes so the kiosk crossfades.
    """
    state = request.app.get("state")
    bcast = request.app.get("broadcaster")
    if state is None or bcast is None:
        return web.json_response({"ok": False, "error": "orchestrator not ready"}, status=503)
    try:
        body = await request.json()
        new_rid = int(body["release_id"])
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": f"bad request: {e!r}"}, status=400)
    rel = discogs_catalog.get_release(new_rid)
    if rel is None:
        return web.json_response({"ok": False, "error": "release not in local catalog"}, status=404)

    prev = state.last_vinyl or {}
    matched_position, matched_title = _resolve_select_track(rel, prev, body)
    payload = _build_select_payload(rel, new_rid, matched_position, matched_title)
    _apply_select_state(state, payload, new_rid)

    log.info(
        "select_release: rid=%s album=%r pos=%s (was rid=%s)",
        new_rid, rel.get("title"), matched_position, prev.get("release_id"),
    )
    await bcast.publish(payload)
    return web.json_response({"ok": True, "release_id": new_rid})
