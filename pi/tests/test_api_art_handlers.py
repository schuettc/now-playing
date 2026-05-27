"""Integration tests for the dual-mode art-* HTTP handlers.

Spins up a minimal aiohttp `Application` with the four handlers
under test (`art_by_name_handler`, `art_candidates_handler`,
`art_override_post_handler`, `art_override_delete_handler`) and
drives them through a TestClient. Verifies the by-name path that
was added for streaming/AirPlay tracks without a Discogs match.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from nowplaying import api, art_overrides  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/art-by-name", api.art_by_name_handler)
    app.router.add_get("/api/art-candidates", api.art_candidates_handler)
    app.router.add_post("/api/art-override", api.art_override_post_handler)
    app.router.add_delete("/api/art-override", api.art_override_delete_handler)
    # The handlers reach into request.app["lyrics_session"] — give them
    # a sentinel that won't actually be used (we patch art_overrides.set
    # and art_picker.fetch_candidates).
    app["lyrics_session"] = object()
    return app


@pytest.fixture(autouse=True)
def _isolate_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(art_overrides, "OVERRIDES_DIR", tmp_path)
    monkeypatch.setattr(art_overrides, "INDEX_PATH", tmp_path / "index.json")
    art_overrides._invalidate_index_cache()


def test_art_by_name_404_when_no_override():
    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(
                "/art-by-name?artist=Hum&album=Downward+Is+Heavenward",
            )
            return resp.status

    assert _run(go()) == 404


def test_art_by_name_400_when_args_missing():
    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/art-by-name?artist=Hum")
            return resp.status

    assert _run(go()) == 400


def test_art_by_name_serves_override(tmp_path):
    """Seed an override on disk, hit /art-by-name, expect the bytes."""
    art_path = tmp_path / "img.jpg"
    art_path.write_bytes(b"\xff\xd8\xff\xe0jpg")
    key = art_overrides.key_for("Test Artist", "Test Album")
    assert key is not None
    (tmp_path / "index.json").write_text(json.dumps({
        key: {
            "url": "https://example/x.jpg",
            "source": "caa",
            "picked_at": "2026-05-14T20:00:00Z",
            "picked_at_epoch": 1747252800,
            "local_path": str(art_path),
            "content_type": "image/jpeg",
        },
    }))
    art_overrides._invalidate_index_cache()

    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(
                "/art-by-name?artist=Test+Artist&album=Test+Album",
            )
            body = await resp.read()
            return resp.status, body, resp.headers.get("Content-Type")

    status, body, ctype = _run(go())
    assert status == 200
    assert body == b"\xff\xd8\xff\xe0jpg"
    assert ctype == "image/jpeg"


def test_art_by_name_self_heals_when_file_missing(tmp_path):
    """Record present but local file gone → 404 + override cleared."""
    key = art_overrides.key_for("Ghost", "Album")
    assert key is not None
    (tmp_path / "index.json").write_text(json.dumps({
        key: {
            "url": "https://example/g.jpg",
            "source": "caa",
            "picked_at": "2026-05-14T20:00:00Z",
            "picked_at_epoch": 1747252800,
            "local_path": str(tmp_path / "missing.jpg"),
            "content_type": "image/jpeg",
        },
    }))
    art_overrides._invalidate_index_cache()

    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/art-by-name?artist=Ghost&album=Album")
            return resp.status

    assert _run(go()) == 404
    # After the request, the override should be cleared.
    assert art_overrides.get("Ghost", "Album") is None


def test_art_candidates_by_name_path():
    """SSE handler accepts artist+album and never tries Discogs."""

    async def _fake_fetch_candidates(
        artist, album, rid, *, session, current_url=None,
    ):
        # Capture args (we want to assert rid is None, current_url
        # threaded through).
        _fake_fetch_candidates.captured = {
            "artist": artist, "album": album, "rid": rid,
            "current_url": current_url,
        }
        yield {"url": "https://x/img.jpg", "source": "caa", "label": "CAA"}

    async def go():
        with patch("nowplaying.art_picker.fetch_candidates", _fake_fetch_candidates):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get(
                    "/api/art-candidates"
                    "?artist=Hum&album=You+%26+Me"
                    "&current_url=%2Fart-cache%2Fdeadbeef",
                )
                body = await resp.text()
                return resp.status, body

    status, body = _run(go())
    assert status == 200
    assert _fake_fetch_candidates.captured["rid"] is None
    assert _fake_fetch_candidates.captured["artist"] == "Hum"
    assert _fake_fetch_candidates.captured["album"] == "You & Me"
    assert _fake_fetch_candidates.captured["current_url"] == "/art-cache/deadbeef"
    assert "https://x/img.jpg" in body
    assert "event: done" in body


def test_art_candidates_400_when_no_args():
    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/art-candidates")
            return resp.status

    assert _run(go()) == 400


def test_art_override_post_by_name_returns_by_name_url():
    """POST with artist+album → override_url is /art-by-name?..., not /art/<rid>."""
    from nowplaying.art_overrides import Override

    fake_override = Override(
        key="abc123",
        url="https://example/x.jpg",
        source="caa",
        picked_at="2026-05-14T20:00:00Z",
        local_path="/tmp/abc.jpg",
        content_type="image/jpeg",
        picked_at_epoch=1747252800,
    )

    async def go():
        with patch(
            "nowplaying.art_overrides.set",
            AsyncMock(return_value=fake_override),
        ):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.post(
                    "/api/art-override",
                    json={
                        "artist": "Hum",
                        "album": "You & Me",
                        "url": "https://example/x.jpg",
                        "source": "caa",
                    },
                )
                return resp.status, await resp.json()

    status, body = _run(go())
    assert status == 200
    assert body["ok"] is True
    assert body["override_url"].startswith("/art-by-name?")
    assert "artist=Hum" in body["override_url"]
    assert "album=You" in body["override_url"]  # url-encoded
    assert "&v=" in body["override_url"]


def test_art_override_post_400_when_no_identity():
    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post(
                "/api/art-override",
                json={"url": "https://example/x.jpg", "source": "caa"},
            )
            return resp.status

    assert _run(go()) == 400


def test_art_override_delete_by_name_path(tmp_path):
    """DELETE with artist+album hits art_overrides.clear with those names."""
    key = art_overrides.key_for("Hum", "You & Me")
    assert key is not None
    (tmp_path / "index.json").write_text(json.dumps({
        key: {
            "url": "https://example/x.jpg",
            "source": "caa",
            "picked_at": "2026-05-14T20:00:00Z",
            "picked_at_epoch": 1747252800,
            "local_path": str(tmp_path / "x.jpg"),
            "content_type": "image/jpeg",
        },
    }))
    art_overrides._invalidate_index_cache()

    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.delete(
                "/api/art-override?artist=Hum&album=You+%26+Me",
            )
            return resp.status, await resp.json()

    status, body = _run(go())
    assert status == 200
    assert body["ok"] is True
    assert body["removed"] is True
    assert art_overrides.get("Hum", "You & Me") is None


def test_art_override_delete_400_when_no_identity():
    async def go():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.delete("/api/art-override")
            return resp.status

    assert _run(go()) == 400


# ---------------------------------------------------------------------------
# art_handler background fetch tests (stats-missing-album-art, PR #154)
# ---------------------------------------------------------------------------

def _make_art_app(tmp_musicbrainz_dir: Path) -> web.Application:
    """Minimal app wiring art_handler at /art/{release_id}."""
    from nowplaying.api.art import art_handler
    from nowplaying.api._paths import MUSICBRAINZ_ART_DIR as _orig_dir  # noqa: F401

    app = web.Application()
    app.router.add_get("/art/{release_id}", art_handler)
    return app


def test_art_handler_schedules_background_task_on_404(monkeypatch, tmp_path):
    """When art_handler finds no cached .jpg and rid_to_album resolves, it
    should schedule a background maybe_cache task and return 404."""
    import nowplaying.api._paths as _paths
    import nowplaying.art_cache as art_cache_mod

    # Point MUSICBRAINZ_ART_DIR at a fresh tmp dir (no .jpg there)
    monkeypatch.setattr(_paths, "MUSICBRAINZ_ART_DIR", tmp_path)

    # Patch rid_to_album to return a known artist/album
    from nowplaying.discogs import catalog as _catalog
    monkeypatch.setattr(_catalog, "rid_to_album", lambda rid: ("Best Coast", "California Nights"))

    # Patch art_overrides.get → None (no override)
    monkeypatch.setattr("nowplaying.art_overrides.get", lambda a, b: None)

    # Track asyncio.create_task calls
    tasks_scheduled = []

    async def go():
        # Reset inflight set for a clean test
        art_cache_mod._mb_inflight.clear()

        app = web.Application()
        from nowplaying.api.art import art_handler
        app.router.add_get("/art/{release_id}", art_handler)

        original_create_task = asyncio.create_task

        def _capture_task(coro, **kwargs):
            tasks_scheduled.append(coro.__qualname__ if hasattr(coro, "__qualname__") else str(coro))
            # Cancel immediately so we don't actually hit MB
            t = original_create_task(coro, **kwargs)
            t.cancel()
            return t

        with patch("nowplaying.api.art.asyncio.create_task", side_effect=_capture_task):
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/art/42")
                return resp.status

    status = _run(go())
    assert status == 404
    assert len(tasks_scheduled) == 1


def test_art_handler_no_task_when_rid_to_album_returns_none(monkeypatch, tmp_path):
    """If rid_to_album can't resolve the release, no background task is scheduled."""
    import nowplaying.api._paths as _paths
    import nowplaying.art_cache as art_cache_mod

    monkeypatch.setattr(_paths, "MUSICBRAINZ_ART_DIR", tmp_path)
    from nowplaying.discogs import catalog as _catalog
    monkeypatch.setattr(_catalog, "rid_to_album", lambda rid: None)
    monkeypatch.setattr("nowplaying.art_overrides.get", lambda a, b: None)

    tasks_scheduled = []

    async def go():
        art_cache_mod._mb_inflight.clear()
        app = web.Application()
        from nowplaying.api.art import art_handler
        app.router.add_get("/art/{release_id}", art_handler)

        with patch("nowplaying.api.art.asyncio.create_task", side_effect=lambda c, **kw: tasks_scheduled.append(c)):
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/art/99")
                return resp.status

    status = _run(go())
    assert status == 404
    assert len(tasks_scheduled) == 0


def test_art_handler_dedup_skips_second_task_for_same_rid(monkeypatch, tmp_path):
    """If a release_id is already in _mb_inflight, no second task is created."""
    import nowplaying.api._paths as _paths
    import nowplaying.art_cache as art_cache_mod

    monkeypatch.setattr(_paths, "MUSICBRAINZ_ART_DIR", tmp_path)
    from nowplaying.discogs import catalog as _catalog
    monkeypatch.setattr(_catalog, "rid_to_album", lambda rid: ("Artist", "Album"))
    monkeypatch.setattr("nowplaying.art_overrides.get", lambda a, b: None)

    tasks_scheduled = []

    async def go():
        # Pre-populate inflight so the handler thinks a task is already running
        art_cache_mod._mb_inflight.clear()
        art_cache_mod._mb_inflight.add(7)

        app = web.Application()
        from nowplaying.api.art import art_handler
        app.router.add_get("/art/{release_id}", art_handler)

        with patch("nowplaying.api.art.asyncio.create_task", side_effect=lambda c, **kw: tasks_scheduled.append(c)):
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/art/7")
                return resp.status

    status = _run(go())
    assert status == 404
    assert len(tasks_scheduled) == 0  # no new task — already in-flight
