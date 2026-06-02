"""Control endpoints — touch-driven admin actions from the kiosk.

POST /control/clear-fingerprints          {release_id, track_position}
POST /control/mark-wrong                  {release_id, track_position}
POST /control/select-release              {release_id}
POST /control/next-track                  {release_id, current_track_position}
POST /api/identify                        {release_id, track_position}
POST /api/pin-track                       {release_id, track_position}
POST /api/dismiss-guess                   {release_id, track_position}
GET  /api/collection/search?q=...
GET  /api/release/{release_id}/tracklist
"""
from __future__ import annotations

from aiohttp import web

from nowplaying import art_cache
from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.vinyl.runtime import to_now_playing_vinyl

from nowplaying.control._shared import (
    _apply_user_track_pin,
    _maybe_schedule_art_fetch,
    _now_iso,
    _safe_art_fetch,
    _tracklist_from_release,
    log,
)
from nowplaying.control.clear_fingerprints import clear_fingerprints
from nowplaying.control.dismiss_guess import dismiss_guess
from nowplaying.control.identify import (
    _apply_identify_payload_overrides,
    _apply_identify_state,
    _build_identify_payload,
    _find_track_on_release,
    identify_clip,
)
from nowplaying.control.mark_wrong import mark_wrong
from nowplaying.control.next import (
    _advance_last_vinyl,
    _advance_pin_if_active,
    _current_side,
    _find_next_on_side,
    _find_position_index,
    _resolve_next_tracklist,
    next_track,
)
from nowplaying.control.pin_track import (
    _bad_pin_request,
    _find_pin_track,
    _resolve_pin_tracklist,
    pin_track,
)
from nowplaying.control.search import (
    _build_artist_groups,
    _build_rerank_ctx,
    _build_search_item,
    _build_search_sql,
    _collect_search_items,
    _fetch_release_tracks,
    _maybe_llm_rerank_items,
    _rank_and_truncate,
    _reorder_by_verdict,
    _score_release,
    _score_token,
    _strip_scores,
    release_tracklist,
    search_collection,
)
from nowplaying.control.select import (
    _apply_select_state,
    _build_select_payload,
    _match_track_by_title,
    _resolve_select_track,
    select_release,
)

__all__ = [
    "art_cache",
    "clear_fingerprints",
    "discogs_catalog",
    "dismiss_guess",
    "identify_clip",
    "log",
    "mark_wrong",
    "next_track",
    "pin_track",
    "register",
    "release_tracklist",
    "search_collection",
    "select_release",
    "to_now_playing_vinyl",
    "_advance_last_vinyl",
    "_advance_pin_if_active",
    "_apply_identify_payload_overrides",
    "_apply_identify_state",
    "_apply_select_state",
    "_apply_user_track_pin",
    "_bad_pin_request",
    "_build_artist_groups",
    "_build_identify_payload",
    "_build_rerank_ctx",
    "_build_search_item",
    "_build_search_sql",
    "_build_select_payload",
    "_collect_search_items",
    "_current_side",
    "_fetch_release_tracks",
    "_find_next_on_side",
    "_find_pin_track",
    "_find_position_index",
    "_find_track_on_release",
    "_match_track_by_title",
    "_maybe_llm_rerank_items",
    "_maybe_schedule_art_fetch",
    "_now_iso",
    "_rank_and_truncate",
    "_reorder_by_verdict",
    "_resolve_next_tracklist",
    "_resolve_pin_tracklist",
    "_resolve_select_track",
    "_safe_art_fetch",
    "_score_release",
    "_score_token",
    "_strip_scores",
    "_tracklist_from_release",
]


def register(app: web.Application) -> None:
    app.router.add_post("/control/clear-fingerprints", clear_fingerprints)
    app.router.add_post("/control/mark-wrong", mark_wrong)
    app.router.add_post("/control/select-release", select_release)
    app.router.add_post("/control/next-track", next_track)
    app.router.add_post("/api/identify", identify_clip)
    app.router.add_post("/api/pin-track", pin_track)
    app.router.add_post("/api/dismiss-guess", dismiss_guess)
    app.router.add_get("/api/collection/search", search_collection)
    app.router.add_get("/api/release/{release_id}/tracklist", release_tracklist)
