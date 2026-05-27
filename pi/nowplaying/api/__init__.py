"""HTTP + WebSocket app that drives the kiosk display.

- GET /            → kiosk/dist/index.html
- GET /assets/*    → kiosk/dist/assets/* (Vite output)
- GET /fixtures/*  → kiosk/dist/fixtures/* (placeholder SVG covers)
- GET /art/<id>    → pi/data/art/<id>.jpg (Discogs cover cache)
- WS  /ws          → unified now-playing payload broadcast

The orchestrator (main.py) imports this and pushes events via Broadcaster.publish().
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from nowplaying.api._paths import (
    ART_DIR,
    KIOSK_DIST,
    MUSICBRAINZ_ART_DIR,
    PI_DIR,
    REPO_ROOT,
    log,
)
from nowplaying.api.art import (
    _resolve_rid_or_artist_album,
    _safe_write_eof,
    _stream_art_candidates,
    art_by_name_handler,
    art_cache_handler,
    art_candidates_handler,
    art_handler,
)
from nowplaying.api.art_overrides import (
    _parse_override_post_body,
    _resolve_override_target,
    art_override_delete_handler,
    art_override_post_handler,
)
from nowplaying.api.broadcaster import Broadcaster, ws_handler
from nowplaying.api.pages import (
    dashboard_page_handler,
    identify_page_handler,
    index_handler,
)
from nowplaying.api.snapshot import (
    _resolve_album_context_key,
    album_context_handler,
    health_handler,
    now_playing_snapshot_handler,
)

__all__ = [
    "ART_DIR",
    "Broadcaster",
    "KIOSK_DIST",
    "MUSICBRAINZ_ART_DIR",
    "PI_DIR",
    "REPO_ROOT",
    "album_context_handler",
    "art_by_name_handler",
    "art_cache_handler",
    "art_candidates_handler",
    "art_handler",
    "art_override_delete_handler",
    "art_override_post_handler",
    "dashboard_page_handler",
    "health_handler",
    "identify_page_handler",
    "index_handler",
    "log",
    "make_app",
    "now_playing_snapshot_handler",
    "parse_args",
    "serve",
    "ws_handler",
    "_parse_override_post_body",
    "_resolve_album_context_key",
    "_resolve_override_target",
    "_resolve_rid_or_artist_album",
    "_safe_write_eof",
    "_stream_art_candidates",
]


async def _on_startup(app: web.Application) -> None:
    # Shared aiohttp session for the album-art proxy upstream fetches.
    # Originally added for LRCLIB lyrics; lyrics removed in `remove-lyrics`
    # but the art proxy reuses the session via `app["lyrics_session"]`.
    # Key name preserved to avoid churning unrelated callers; consider
    # renaming to `http_session` in a future cleanup.
    import aiohttp as _aiohttp
    app["lyrics_session"] = _aiohttp.ClientSession(
        timeout=_aiohttp.ClientTimeout(total=15.0),
        headers={"User-Agent": "now-playing/1.0 (https://github.com/schuettc/now-playing)"},
    )


async def _on_cleanup(app: web.Application) -> None:
    sess = app.get("lyrics_session")
    if sess is not None:
        await sess.close()


def make_app() -> web.Application:
    app = web.Application()
    app["broadcaster"] = Broadcaster()
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    app.router.add_get("/health", health_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/art/{release_id}", art_handler)
    app.router.add_get("/art-by-name", art_by_name_handler)
    app.router.add_get("/art-cache/{key}", art_cache_handler)
    app.router.add_get("/api/art-candidates", art_candidates_handler)
    app.router.add_post("/api/art-override", art_override_post_handler)
    app.router.add_delete("/api/art-override", art_override_delete_handler)
    app.router.add_get("/api/album-context", album_context_handler)
    app.router.add_get("/api/now-playing", now_playing_snapshot_handler)
    app.router.add_get("/identify", identify_page_handler)
    app.router.add_get("/dashboard", dashboard_page_handler)
    app.router.add_get("/", index_handler)

    if KIOSK_DIST.exists():
        app.router.add_static("/assets", KIOSK_DIST / "assets", show_index=False)
        fixtures = KIOSK_DIST / "fixtures"
        if fixtures.exists():
            app.router.add_static("/fixtures", fixtures, show_index=False)
    return app


async def serve(
    host: str = "0.0.0.0",
    port: int = 8080,
    app: web.Application | None = None,
) -> tuple[web.Application, web.AppRunner]:
    """Start the aiohttp app. Returns (app, runner). Caller must runner.cleanup() on shutdown.

    If `app` is provided, use it directly (lets callers register additional
    routes before the router is frozen by AppRunner.setup). Otherwise builds
    a default app via make_app().
    """
    if app is None:
        app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("nowplaying http+ws listening on http://%s:%d", host, port)
    if not KIOSK_DIST.exists():
        log.warning("kiosk/dist not present — UI will return 503 until built")
    return app, runner


def parse_args() -> tuple[str, int]:
    import argparse, os

    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("NP_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("NP_PORT", "8080")))
    a = p.parse_args()
    return a.host, a.port


async def _serve_only() -> None:
    """Run just the HTTP server (no orchestrator). Useful for static-only smoke tests."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host, port = parse_args()
    _app, runner = await serve(host, port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(_serve_only())
