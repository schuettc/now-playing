"""Sonos events are handled one at a time, in arrival order: a slow queue
poll for an older event must not publish over a newer track.
"""
from __future__ import annotations

import asyncio
from unittest import mock

from nowplaying.main import Orchestrator, State


def _event(title: str) -> dict:
    return {
        "ts": "2026-11-01T12:00:00Z", "state": "PLAYING",
        "source": "stream", "title": title, "artist": "Talking Heads",
    }


def test_slow_queue_poll_cannot_publish_an_older_track_last():
    state = State()
    bcast = mock.MagicMock()
    published: list[str] = []

    async def publish(payload):
        published.append(payload["title"])
        return True

    bcast.publish = publish
    orch = Orchestrator(
        state=state, bcast=bcast, sonos_coord=mock.MagicMock(),
        stop=mock.MagicMock(), llm=mock.MagicMock(enabled=False),
    )

    delays = iter([0.05, 0])

    async def poll_queue(_coord, limit=16):
        # The first (older) event's poll is the slow one.
        await asyncio.sleep(next(delays))
        return []

    async def scenario():
        older = asyncio.create_task(orch.on_sonos_event(_event("Road to Nowhere")))
        await asyncio.sleep(0)
        newer = asyncio.create_task(orch.on_sonos_event(_event("And She Was")))
        await asyncio.gather(older, newer)

    with (
        mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()),
        mock.patch(
            "nowplaying.orchestrator._sonos_handlers.signal_capture",
            return_value=True,
        ),
        mock.patch(
            "nowplaying.orchestrator._publish_enrichment.poll_queue", new=poll_queue,
        ),
    ):
        asyncio.run(scenario())

    assert published == ["Road to Nowhere", "And She Was"]
    assert state.last_vinyl["title"] == "And She Was"
