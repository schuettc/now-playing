"""Snapshot / health / album-context read endpoints."""
from __future__ import annotations

from aiohttp import web

from nowplaying.api.broadcaster import Broadcaster


async def health_handler(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def now_playing_snapshot_handler(request: web.Request) -> web.Response:
    """Return the Broadcaster's most recent now-playing payload, if any.

    Used by static pages (e.g. /identify) that need a one-shot snapshot of
    what's currently playing without opening a WebSocket. Always 200; if
    nothing has been broadcast yet, `payload` is null.
    """
    bcast: Broadcaster = request.app["broadcaster"]
    last = bcast._last
    return web.json_response(
        {"ok": True, "payload": last},
        headers={"Cache-Control": "no-store"},
    )


async def album_context_handler(request: web.Request) -> web.Response:
    """Return a Wikipedia summary blurb for the playing album.

    Dual-mode:
      - vinyl path:    ?release_id=N (artist, album optional hints for
                       the live-fetch-on-cache-miss case)
      - by-name path:  ?artist=X&album=Y (streaming / AirPlay tracks
                       without a Discogs match — cached under a stable
                       artcache.key_for hash so future plays hit cache)

    Response (always 200):
      {"ok": true, "release_id": N|null, "cache_key": str,
       "summary": str|null, "url": str|null, "title": str|null}
    """
    from nowplaying import artcache, wiki  # local import to avoid import-time cost

    raw_rid = request.query.get("release_id", "")
    artist = (request.query.get("artist") or "").strip()
    album = (request.query.get("album") or "").strip()
    release_id, cache_key = _resolve_album_context_key(raw_rid, artist, album, artcache)

    cached = wiki.cached_summary(cache_key)
    if cached is None and artist and album:
        cached = await wiki.get_or_fetch(cache_key, artist, album)
    summary = cached.get("summary") if cached else None
    url = cached.get("url") if cached else None
    title = cached.get("title") if cached else None
    return web.json_response({
        "ok": True,
        "release_id": release_id,
        "cache_key": str(cache_key),
        "summary": summary,
        "url": url,
        "title": title,
    })


def _resolve_album_context_key(
    raw_rid: str, artist: str, album: str, artcache,
) -> tuple[int | None, int | str]:
    """Return (release_id, cache_key) for an album-context request. Raises
    HTTPBadRequest when neither release_id nor artist+album is usable."""
    if raw_rid.isdigit():
        release_id = int(raw_rid)
        return release_id, release_id
    if artist and album:
        hashed = artcache.key_for(artist, album)
        if not hashed:
            raise web.HTTPBadRequest(reason="artist and album required")
        # Prefix so a hashed (artist, album) entry can't collide with a
        # purely-numeric release_id on the wiki cache filesystem.
        return None, f"name-{hashed}"
    raise web.HTTPBadRequest(reason="release_id or artist+album required")
