"""Static / SPA-shell page handlers."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from nowplaying.api._paths import KIOSK_DIST


async def index_handler(_request: web.Request) -> web.Response:
    index = KIOSK_DIST / "index.html"
    if not index.exists():
        return web.Response(
            status=503,
            text=(
                "kiosk/dist not built yet.\n"
                "On a dev machine: cd kiosk && pnpm install && pnpm build\n"
                "Then commit and `git pull` on the Pi.\n"
            ),
        )
    # Cache-Control: no-store ensures Chromium always re-fetches the
    # entry document, so a freshly deployed kiosk bundle is picked up on
    # the next page load. Hashed JS/CSS inside this document still cache
    # normally via their default headers.
    return web.FileResponse(index, headers={"Cache-Control": "no-store"})


async def identify_page_handler(_request: web.Request) -> web.Response:
    # `/identify` is now a client-side route inside the kiosk SPA bundle
    # (see `kiosk/src/routes/Identify.tsx`). Serve the same `index.html`
    # the root path serves — Wouter mounts the right component once the
    # bundle hydrates. Removed the hand-rolled `static/identify.html` in
    # `identify-migrate-to-react`.
    return await index_handler(_request)


async def dashboard_page_handler(_request: web.Request) -> web.Response:
    page = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"
    if not page.exists():
        return web.Response(status=503, text="dashboard page missing")
    return web.FileResponse(page)
