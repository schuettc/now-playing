"""/api/album-context must file a blurb under the release it describes."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import api, wiki  # noqa: E402
from nowplaying.discogs import catalog  # noqa: E402


def _get(path: str) -> dict:
    async def go():
        app = web.Application()
        app.router.add_get("/api/album-context", api.album_context_handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(path)
            return await resp.json()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def _capture_fetches(monkeypatch) -> list[tuple]:
    seen: list[tuple] = []

    async def fake(cache_key, artist, album, **_kw):
        seen.append((cache_key, artist, album))
        return {"summary": "s", "url": "u", "title": "t"}

    monkeypatch.setattr(wiki, "cached_summary", lambda _k: None)
    monkeypatch.setattr(wiki, "get_or_fetch", fake)
    return seen


def test_release_lookup_uses_the_catalog_names_not_the_query(monkeypatch):
    seen = _capture_fetches(monkeypatch)
    monkeypatch.setattr(
        catalog, "rid_to_album", lambda rid: ("Pink Floyd", "Animals"),
    )
    _get("/api/album-context?release_id=42&artist=Someone+Else&album=Other")
    assert seen == [(42, "Pink Floyd", "Animals")]


def test_unknown_release_is_not_fetched_from_client_names(monkeypatch):
    seen = _capture_fetches(monkeypatch)
    monkeypatch.setattr(catalog, "rid_to_album", lambda rid: None)
    body = _get("/api/album-context?release_id=42&artist=Someone+Else&album=Other")
    assert seen == []
    assert body["summary"] is None
