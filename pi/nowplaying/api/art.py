"""Album-art serving + SSE candidate stream + cached upstream proxy."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web

from nowplaying.api._paths import MUSICBRAINZ_ART_DIR, log
from nowplaying.net_allowlist import is_allowed_upstream as _is_allowed_upstream


async def art_handler(request: web.Request) -> web.StreamResponse:
    from nowplaying import art_overrides
    from nowplaying.discogs import catalog as _catalog

    release_id = request.match_info["release_id"]
    if not release_id.isdigit():
        raise web.HTTPNotFound()
    rid = int(release_id)

    # Override-first: if the user has picked art for this album, serve
    # the local file. Resolution release_id → (artist, album) goes
    # through `rid_to_album`'s lru_cache so a hot album doesn't hit
    # SQLite on every /art/<id> request; first miss is wrapped in
    # to_thread to keep the event loop unblocked.
    artist_album = await asyncio.to_thread(_catalog.rid_to_album, rid)
    if artist_album:
        artist, album = artist_album
        ov = await asyncio.to_thread(art_overrides.get, artist, album)
        if ov is not None and ov.local_path:
            local = Path(ov.local_path)
            if local.exists():
                # FileResponse streams the file off disk and honors
                # If-None-Match / If-Modified-Since automatically — no
                # 8 MiB memory spike per request, and 304s on revisit.
                return web.FileResponse(
                    local,
                    headers={
                        "Content-Type": ov.content_type,
                        "Cache-Control": "public, max-age=86400, must-revalidate",
                        "ETag": f'"ov-{ov.picked_at_epoch}"',
                    },
                )
            # Override record exists but local file is missing — self-heal
            # and fall through to CAA so the user sees something rather
            # than a 404 they can't act on without opening the picker.
            log.info(
                "art_overrides: local file missing for key=%s, clearing", ov.key,
            )
            await asyncio.to_thread(art_overrides.clear, artist, album)

    cache_headers = {"Cache-Control": "public, max-age=86400"}
    candidate = MUSICBRAINZ_ART_DIR / f"{release_id}.jpg"
    if candidate.exists():
        return web.FileResponse(candidate, headers=cache_headers)

    # No cached art yet. If we resolved artist+album above, schedule a
    # background MusicBrainz fetch so the art appears on the next request
    # (e.g. the dashboard's 30-second auto-refresh). This brings the stats
    # panel into alignment with the now-playing path, which already triggers
    # maybe_cache during recognition.
    #
    # In-flight guard: skip if a task for this release_id is already queued
    # or running. This prevents the dashboard loading 10 cards simultaneously
    # from queuing 10 redundant tasks through the rate-limiting semaphore.
    if artist_album:
        from nowplaying import art_cache

        if rid not in art_cache._mb_inflight:
            art_cache._mb_inflight.add(rid)

            async def _fetch_and_cleanup() -> None:
                try:
                    await art_cache.maybe_cache(rid, artist_album[0], artist_album[1])
                except Exception as exc:  # noqa: BLE001 — log+swallow; caller already gone
                    log.warning("art_handler: background MB fetch failed for rid=%s: %r", rid, exc)
                finally:
                    art_cache._mb_inflight.discard(rid)

            asyncio.create_task(_fetch_and_cleanup())

    raise web.HTTPNotFound()


async def art_by_name_handler(request: web.Request) -> web.StreamResponse:
    """Serve an art override keyed by ``(artist, album)`` instead of
    Discogs ``release_id``. Used by the kiosk for streaming/AirPlay
    tracks that don't match a record in the user's collection.

    There is no MusicBrainz CAA fallback file: ``MUSICBRAINZ_ART_DIR``
    is keyed strictly by ``release_id`` (see ``pi/nowplaying/art_cache.py``),
    so there's no on-disk cache for non-matched tracks. The kiosk
    only fetches this URL *after* the user has placed an override
    (per ``_rewrite_art_url_for_overrides`` in main.py), so a 404
    here means the override was deleted out from under the rewrite —
    self-heal and return.
    """
    from nowplaying import art_overrides

    artist = (request.query.get("artist") or "").strip()
    album = (request.query.get("album") or "").strip()
    if not artist or not album:
        raise web.HTTPBadRequest(reason="artist and album required")

    ov = await asyncio.to_thread(art_overrides.get, artist, album)
    if ov is not None and ov.local_path:
        local = Path(ov.local_path)
        if local.exists():
            return web.FileResponse(
                local,
                headers={
                    "Content-Type": ov.content_type,
                    "Cache-Control": "public, max-age=86400, must-revalidate",
                    "ETag": f'"ov-{ov.picked_at_epoch}"',
                },
            )
        # Override record exists but the local file is gone — clear
        # the orphan record so the next request doesn't keep finding it.
        log.info(
            "art_overrides: local file missing for key=%s (by-name path), clearing",
            ov.key,
        )
        await asyncio.to_thread(art_overrides.clear, artist, album)
    raise web.HTTPNotFound()


async def _resolve_rid_or_artist_album(
    rid_raw: str, artist_q: str, album_q: str,
) -> tuple[int | None, str, str]:
    """Resolve a dual-mode (release_id vs artist+album) query into (rid, artist, album).
    Raises HTTP errors on invalid/missing input or missing catalog entry."""
    from nowplaying.discogs import catalog as _catalog

    if rid_raw.isdigit():
        rid = int(rid_raw)
        artist_album = await asyncio.to_thread(_catalog.rid_to_album, rid)
        if not artist_album:
            raise web.HTTPNotFound(reason="release not in local catalog")
        artist, album = artist_album
        return rid, artist, album
    if artist_q and album_q:
        return None, artist_q, album_q
    raise web.HTTPBadRequest(reason="release_id or artist+album required")


async def art_candidates_handler(request: web.Request) -> web.StreamResponse:
    """SSE stream of candidate covers. The kiosk picker consumes this with
    EventSource; each ``data:`` frame is one candidate, and the final
    ``event: done`` frame closes the stream.

    Dual-mode: accept either ``?release_id=N`` (vinyl path — resolves
    via Discogs catalog) OR ``?artist=X&album=Y`` (streaming/AirPlay
    path — no Discogs match required). When both are present,
    release_id wins (Discogs-first precedence).
    """
    from nowplaying import art_picker

    rid_raw = request.query.get("release_id", "")
    artist_q = (request.query.get("artist") or "").strip()
    album_q = (request.query.get("album") or "").strip()
    # Optional: the kiosk's currently rendered art_url. Used as the
    # "Current" candidate's fallback URL on the by-name path so the
    # tile shows the streaming-service art instead of 404-ing.
    current_url_q = (request.query.get("current_url") or "").strip() or None
    rid, artist, album = await _resolve_rid_or_artist_album(rid_raw, artist_q, album_q)

    session = request.app.get("lyrics_session")
    if session is None:
        raise web.HTTPInternalServerError(reason="no session")

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    try:
        candidates = art_picker.fetch_candidates(
            artist, album, rid, session=session, current_url=current_url_q,
        )
        await _stream_art_candidates(resp, candidates)
    finally:
        await _safe_write_eof(resp)
    return resp


async def _stream_art_candidates(resp: web.StreamResponse, candidates) -> None:
    """Pump an async iterable of candidate dicts into the SSE response.
    Caller owns the response; on CancelledError we propagate so aiohttp can
    tear down."""
    try:
        async for cand in candidates:
            payload = json.dumps(cand)
            await resp.write(f"data: {payload}\n\n".encode("utf-8"))
        await resp.write(b"event: done\ndata: {}\n\n")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("art-candidates stream failed: %r", e)


async def _safe_write_eof(resp: web.StreamResponse) -> None:
    """Close the SSE stream, swallowing the two errors that happen when the
    peer has already gone away — these are noise, not bugs."""
    try:
        await resp.write_eof()
    except (ConnectionResetError, RuntimeError) as e:
        # Peer disconnected mid-stream (ConnectionResetError) or the response
        # is already closed (RuntimeError from aiohttp). Either way the
        # client is gone — log at debug so we can still see it during dev.
        log.debug("art-candidates write_eof skipped: %r", e)


async def art_cache_handler(request: web.Request) -> web.StreamResponse:
    from nowplaying import artcache  # local import: avoid cycles at module load
    key = request.match_info["key"]
    if not artcache.is_valid_key(key):
        raise web.HTTPNotFound()
    cached = artcache.read_cached(key)
    if cached is not None:
        data, ctype = cached
        return web.Response(
            body=data,
            content_type=ctype,
            headers={"Cache-Control": "public, max-age=2592000"},
        )
    upstream = request.query.get("u")
    if not upstream:
        raise web.HTTPNotFound()
    if not _is_allowed_upstream(upstream):
        raise web.HTTPBadRequest(reason="upstream not in allowlist")
    session = request.app.get("lyrics_session")  # reused: same shared session
    if session is None:
        raise web.HTTPInternalServerError(reason="no session")
    data, ctype, status = await artcache.fetch_and_cache(
        key, upstream, session=session,
    )
    if status == 404 or data is None:
        # 404 = the upstream genuinely doesn't have art; tell the kiosk
        # directly so it can render NO ART without spinning. 502 for
        # transient upstream failures.
        raise web.HTTPNotFound() if status == 404 else web.HTTPBadGateway()
    return web.Response(
        body=data,
        content_type=ctype,
        headers={"Cache-Control": "public, max-age=2592000"},
    )
