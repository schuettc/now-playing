"""Middleware that keeps other sites' pages from driving the kiosk's API.

Nothing here is authenticated, so a page on any site, open in any browser
on the LAN, could otherwise POST to the control endpoints. Browsers attach
``Origin`` to every cross-site state-changing request, and can only send a
JSON content type cross-site after a preflight this app never answers.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from aiohttp import web

_SAFE_METHODS = frozenset({"GET", "HEAD"})


@web.middleware
async def request_guard(request: web.Request, handler):
    """Reject a state-changing request whose ``Origin`` is another host,
    and a POST whose body is not declared as JSON. Clients that send no
    ``Origin`` (curl, scripts on the LAN) pass the first check."""
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc.lower() != request.host.lower():
            raise web.HTTPForbidden(reason="cross-origin request")
        if (
            request.method == "POST"
            and request.body_exists
            and request.content_type != "application/json"
        ):
            raise web.HTTPUnsupportedMediaType(reason="application/json required")
    return await handler(request)
