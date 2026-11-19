"""Cross-site request guard on the kiosk's HTTP app."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import api  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _app(calls: list[str]) -> web.Application:
    app = api.make_app()

    async def mutate(request: web.Request) -> web.Response:
        calls.append(request.method)
        return web.json_response({"ok": True})

    app.router.add_post("/control/thing", mutate)
    app.router.add_delete("/control/thing", mutate)
    app.router.add_route("NOTIFY", "/control/thing", mutate)
    return app


def _request(method: str, **kw) -> tuple[int, list[str]]:
    calls: list[str] = []

    async def go():
        async with TestClient(TestServer(_app(calls))) as client:
            own = f"http://{client.host}:{client.port}"
            headers = {
                k: v.replace("SELF", own) for k, v in kw.pop("headers", {}).items()
            }
            resp = await client.request(method, "/control/thing", headers=headers, **kw)
            return resp.status

    return _run(go()), calls


def test_cross_origin_post_is_rejected_before_the_handler():
    status, calls = _request(
        "POST", json={"release_id": 1}, headers={"Origin": "http://evil.example"},
    )
    assert status == 403
    assert calls == []


def test_cross_origin_delete_is_rejected():
    status, calls = _request("DELETE", headers={"Origin": "http://evil.example"})
    assert status == 403
    assert calls == []


def test_same_origin_json_post_passes():
    status, calls = _request("POST", json={"a": 1}, headers={"Origin": "SELF"})
    assert status == 200
    assert calls == ["POST"]


def test_post_body_that_is_not_json_is_rejected():
    """The no-preflight form a foreign page can send: text/plain body."""
    status, calls = _request(
        "POST", data='{"release_id":1}', headers={"Content-Type": "text/plain"},
    )
    assert status == 415
    assert calls == []


def test_bodyless_post_without_origin_passes():
    status, calls = _request("POST")
    assert status == 200
    assert calls == ["POST"]


def test_notify_without_origin_passes():
    status, calls = _request(
        "NOTIFY", data="<e:propertyset/>", headers={"Content-Type": "text/xml"},
    )
    assert status == 200
    assert calls == ["NOTIFY"]


def test_cross_origin_get_is_untouched():
    async def go():
        async with TestClient(TestServer(api.make_app())) as client:
            resp = await client.get("/health", headers={"Origin": "http://evil.example"})
            return resp.status

    assert _run(go()) == 200
