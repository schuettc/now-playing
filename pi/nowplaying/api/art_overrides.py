"""POST/DELETE endpoints for user art overrides."""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlencode

from aiohttp import web


async def _parse_override_post_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(reason="invalid JSON body")
    url = (body.get("url") or "").strip()
    source = (body.get("source") or "").strip()
    if not url or not source:
        raise web.HTTPBadRequest(reason="url, source required")
    return {
        "rid_raw": body.get("release_id"),
        "artist": (body.get("artist") or "").strip(),
        "album": (body.get("album") or "").strip(),
        "url": url,
        "source": source,
    }


async def _resolve_override_target(
    rid_raw, artist_body: str, album_body: str, epoch: int,
) -> tuple[str, str, str]:
    """Return (artist, album, override_url). Raises HTTP* on bad input."""
    from nowplaying.discogs import catalog as _catalog

    if isinstance(rid_raw, int) and rid_raw > 0:
        artist_album = await asyncio.to_thread(_catalog.rid_to_album, rid_raw)
        if not artist_album:
            raise web.HTTPNotFound(reason="release not in local catalog")
        artist, album = artist_album
        return artist, album, f"/art/{rid_raw}?v={epoch}"
    if artist_body and album_body:
        qs = urlencode({"artist": artist_body, "album": album_body})
        return artist_body, album_body, f"/art-by-name?{qs}&v={epoch}"
    raise web.HTTPBadRequest(reason="release_id or artist+album required")


async def art_override_post_handler(request: web.Request) -> web.Response:
    """Save an art override. Dual-mode body:
      - vinyl path:    {release_id, url, source}
      - by-name path:  {artist, album, url, source}
    When both are present, release_id wins. The response ``override_url``
    switches shape per path so the kiosk knows which URL to fetch on
    its next render.
    """
    from nowplaying import art_overrides

    parsed = await _parse_override_post_body(request)
    epoch = int(time.time())
    artist, album, override_url = await _resolve_override_target(
        parsed["rid_raw"], parsed["artist"], parsed["album"], epoch,
    )

    session = request.app.get("lyrics_session")
    if session is None:
        raise web.HTTPInternalServerError(reason="no session")

    try:
        ov = await art_overrides.set(
            artist, album, parsed["url"], parsed["source"], session=session,
        )
    except art_overrides.OverrideError as e:
        return web.json_response(
            {"ok": False, "error": "override_failed", "reason": e.reason},
            status=e.status,
        )
    return web.json_response({
        "ok": True,
        "key": ov.key,
        "override_url": override_url,
    })


async def art_override_delete_handler(request: web.Request) -> web.Response:
    """Clear an art override. Dual-mode:
      - vinyl path:    ?release_id=N
      - by-name path:  ?artist=X&album=Y
    """
    from nowplaying import art_overrides
    from nowplaying.discogs import catalog as _catalog

    rid_raw = request.query.get("release_id", "")
    artist_q = (request.query.get("artist") or "").strip()
    album_q = (request.query.get("album") or "").strip()
    if rid_raw.isdigit():
        rid = int(rid_raw)
        artist_album = await asyncio.to_thread(_catalog.rid_to_album, rid)
        if not artist_album:
            raise web.HTTPNotFound(reason="release not in local catalog")
        artist, album = artist_album
    elif artist_q and album_q:
        artist, album = artist_q, album_q
    else:
        raise web.HTTPBadRequest(reason="release_id or artist+album required")
    removed = await asyncio.to_thread(art_overrides.clear, artist, album)
    return web.json_response({"ok": True, "removed": removed})
