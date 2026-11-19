"""A play is remembered as scrobbled only once Last.fm has accepted it."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from nowplaying.history import scrobble as hs


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(hs, "_scrobbled_ids", {})
    monkeypatch.setattr(hs, "_scrobbling_ids", set())


def _ctx():
    row = {"id": 7, "started_at": 1000, "ended_at": 1300}
    return hs._ScrobbleCtx(row, 7, "Hum", "Stars", "You'd Prefer an Astronaut", None)


def test_rejected_scrobble_is_sent_again_on_the_next_heartbeat(monkeypatch):
    send = AsyncMock(side_effect=[False, True, True])
    monkeypatch.setattr(hs._scrobble, "scrobble", send)

    async def go():
        for _ in range(3):
            await hs._maybe_scrobble(_ctx(), 300)

    asyncio.run(go())
    assert send.await_count == 2
