"""HTTP endpoints surfacing play history aggregations."""
from __future__ import annotations

import asyncio
import time

from aiohttp import web

from .queries import get_album_stats, heatmap, recent, top_albums


async def _album_stats_handler(request: web.Request) -> web.Response:
    raw = request.query.get("release_id")
    if not raw:
        return web.json_response(
            {"ok": False, "error": "release_id is required"}, status=400
        )
    try:
        rid = int(raw)
    except ValueError:
        return web.json_response(
            {"ok": False, "error": "release_id must be an integer"}, status=400
        )
    stats = await asyncio.to_thread(get_album_stats, rid)
    return web.json_response({"ok": True, "stats": stats})


async def _history_handler(request: web.Request) -> web.Response:
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 500))
    except ValueError:
        limit = 50
    since_raw = request.query.get("since")
    since: int | None = None
    if since_raw:
        try:
            since = int(since_raw)
        except ValueError:
            return web.json_response(
                {"error": "since must be a unix-seconds integer"}, status=400
            )
    plays = await asyncio.to_thread(recent, limit, since)
    return web.json_response({"plays": plays})


def _parse_int(value: str | None, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except ValueError:
        return default
    return max(lo, min(n, hi))


async def _top_albums_handler(request: web.Request) -> web.Response:
    since_days = _parse_int(request.query.get("since_days"), 30, 1, 365)
    limit = _parse_int(request.query.get("limit"), 10, 1, 100)
    since_ts = int(time.time()) - since_days * 86400
    items = await asyncio.to_thread(top_albums, since_ts, limit)
    return web.json_response({"since_days": since_days, "items": items})


async def _recent_handler(request: web.Request) -> web.Response:
    limit = _parse_int(request.query.get("limit"), 50, 1, 500)
    plays = await asyncio.to_thread(recent, limit, None)
    return web.json_response({"plays": plays})


async def _heatmap_handler(request: web.Request) -> web.Response:
    days = _parse_int(request.query.get("days"), 90, 1, 365)
    since_ts = int(time.time()) - days * 86400
    items = await asyncio.to_thread(heatmap, since_ts)
    return web.json_response({"days": days, "items": items})


def register(app: web.Application) -> None:
    app.router.add_get("/history", _history_handler)
    app.router.add_get("/api/album-stats", _album_stats_handler)
    app.router.add_get("/api/history/top-albums", _top_albums_handler)
    app.router.add_get("/api/history/recent", _recent_handler)
    app.router.add_get("/api/history/heatmap", _heatmap_handler)
