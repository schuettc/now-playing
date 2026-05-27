"""POST /api/identify — user identified what's playing from search."""
from __future__ import annotations

import asyncio
import time

from aiohttp import web

from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.vinyl import promotion
from nowplaying.vinyl.runtime import to_now_playing_vinyl

from nowplaying.control._shared import (
    _apply_user_track_pin,
    _audible_edge_unix_ts,
    _is_fresh_side_first_track_for_pin,
    _maybe_schedule_art_fetch,
    _now_iso,
    _tracklist_from_release,
    log,
)


def _find_track_on_release(rel: dict, pos: str) -> dict | None:
    """Return the track dict whose position matches ``pos`` (case-insensitive)."""
    target = pos.strip().upper()
    for t in (rel.get("tracks") or []):
        if (t.get("position") or "").strip().upper() == target:
            return t
    return None


def _build_identify_payload(
    rel: dict, rid: int, pos: str, matched: dict, method: str,
) -> dict:
    """Construct the to_now_playing_vinyl payload for an identify-style publish."""
    synthetic = {
        "ts": _now_iso(),
        "title": matched.get("title"),
        "artist": rel.get("artist"),
        "album": rel.get("title"),
        "year": rel.get("year"),
        "label": rel.get("label"),
        "catno": rel.get("catno"),
        "track_position": pos,
        "release_id": rid,
        "match_method": method,
        "match_confidence": "user",
        "tracklist": _tracklist_from_release(rel),
    }
    return to_now_playing_vinyl(synthetic)


def _apply_identify_payload_overrides(
    payload: dict, state, matched: dict, now_iso: str,
) -> None:
    """Set state/source/duration/side fields onto an identify payload."""
    payload["state"] = "PLAYING"
    if state.sonos_source in ("vinyl", "airplay"):
        payload["source"] = state.sonos_source
    else:
        payload["source"] = "vinyl"
    payload["track_started_at"] = now_iso
    matched_duration = matched.get("duration_seconds")
    if matched_duration is not None:
        payload["duration_seconds"] = matched_duration
    matched_side = matched.get("side")
    if matched_side:
        payload["side"] = matched_side


def _apply_identify_state(
    state, payload: dict, rid: int, pos: str, matched: dict, now_iso: str,
) -> None:
    """Mutate orchestrator state to reflect a user-identified track.

    Note: ``state.track_started_at`` is reset to ``now_iso`` *before*
    calling ``_apply_user_track_pin`` so that ``compute_pin_duration``
    would always see elapsed=0 here.  The identify flow is an authoritative
    "this track just started" signal (user picked a release+track from
    search), so using the full duration minus the safety margin is correct.
    We pass ``None`` as ``track_started_at_iso`` explicitly to let the
    helper treat this as a fresh-start scenario.
    """
    state.track_started_at = now_iso
    state.last_vinyl = payload
    state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
    state.pending_shazam_only.clear()
    state.predicted_position = None
    _apply_user_track_pin(state, rid, pos, matched, track_started_at_iso=None)
    # Retroactive coverage backfill — only fire for the first track of
    # a fresh side, where the audible-edge bounds a single track. For
    # tracks 2+ on a side, forward-only pin coverage is the safer
    # choice (avoids cross-attributing prior-track audio).
    if _is_fresh_side_first_track_for_pin(state, rid, pos):
        edge_ts = _audible_edge_unix_ts(state)
        if edge_ts is not None:
            asyncio.create_task(
                promotion.schedule_backfill_promotions(
                    release_id=rid,
                    track_position=pos,
                    audible_edge_unix_ts=edge_ts,
                    pin_unix_ts=int(time.time()),
                    duration_s=matched.get("duration_seconds"),
                ),
            )
        else:
            log.info(
                "identify-backfill: no recent audible-edge (release=%s pos=%s)",
                rid, pos,
            )
    else:
        log.info(
            "identify-backfill: not first-track-of-side; skipping (release=%s pos=%s)",
            rid, pos,
        )


async def identify_clip(request: web.Request) -> web.Response:
    """User identified what's playing — publish PLAYING with the chosen
    release+track.

    Body: {release_id, track_position}

    Called by the kiosk's `/identify` search-and-pick flow ("Wrong album",
    "Manually set what's playing"). The clip-graduation pathway (audfprint
    promotion) was deleted in 2026-05-14's audfprint-zombie-cleanup, so
    this endpoint is now pure publish.
    """
    state = request.app.get("state")
    bcast = request.app.get("broadcaster")
    if state is None or bcast is None:
        return web.json_response({"ok": False, "error": "orchestrator not ready"}, status=503)
    try:
        body = await request.json()
        rid = int(body["release_id"])
        pos = str(body["track_position"])
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": f"bad request: {e!r}"}, status=400)

    rel = discogs_catalog.get_release(rid)
    if rel is None:
        return web.json_response({"ok": False, "error": "release not in local catalog"}, status=404)
    matched = _find_track_on_release(rel, pos)
    if matched is None:
        return web.json_response(
            {"ok": False, "error": f"track {pos} not on release {rid}"}, status=404,
        )

    payload = _build_identify_payload(rel, rid, pos, matched, "user-identified")
    now_iso = _now_iso()
    _apply_identify_payload_overrides(payload, state, matched, now_iso)
    _apply_identify_state(state, payload, rid, pos, matched, now_iso)

    log.info(
        "identify: release=%s pos=%s title=%r duration=%s (pin set)",
        rid, pos, matched.get("title"), matched.get("duration_seconds"),
    )
    await bcast.publish(payload)
    _maybe_schedule_art_fetch(rid, payload)

    return web.json_response({
        "ok": True,
        "release_id": rid,
        "track_position": pos,
    })
