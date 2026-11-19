"""Every MusicBrainz request goes through the one process-wide 1 req/s
gate, ``art_cache.musicbrainz_slot``."""
from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from nowplaying import art_cache, coverart
from nowplaying.discovery import musicbrainz_lookup as mbl


class _Resp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return {}


class _Session:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        assert "musicbrainz.org" in url
        return _Resp()


_CALLS = {
    "release search": lambda: coverart.fetch_release_mbid("Neil Young", "Harvest"),
    "candidate search": lambda: coverart.search_release_candidates(
        "Neil Young", "Harvest",
    ),
    "release recordings": lambda: coverart.fetch_release_recordings("mb-1"),
    "recording duration": lambda: coverart.fetch_recording_duration("rec-1"),
    "isrc recording length": lambda: mbl.recording_length_by_isrc("USRE10001234"),
    "isrc release": lambda: mbl.lookup_by_isrc("USRE10001234", "Harvest"),
    "full release": lambda: mbl._fetch_release_full("mb-1", timeout_s=1.0),
}


@pytest.mark.parametrize("name", list(_CALLS))
def test_musicbrainz_request_takes_the_shared_slot(name, monkeypatch):
    monkeypatch.setattr(coverart.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(coverart, "_negative_cached", lambda a, b: False)
    entered = []

    @contextlib.asynccontextmanager
    async def _slot():
        entered.append(name)
        yield

    monkeypatch.setattr(art_cache, "musicbrainz_slot", _slot)
    asyncio.run(_CALLS[name]())
    assert entered


def test_slot_spaces_requests_by_the_minimum_interval(monkeypatch):
    monkeypatch.setattr(art_cache, "MUSICBRAINZ_MIN_INTERVAL_S", 0.2)
    monkeypatch.setattr(art_cache, "_mb_semaphore", None)
    monkeypatch.setattr(art_cache, "_mb_last_request_at", None)
    starts = []

    async def _request():
        async with art_cache.musicbrainz_slot():
            starts.append(time.monotonic())

    async def _run():
        await asyncio.gather(_request(), _request(), _request())

    asyncio.run(_run())
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(gap >= 0.19 for gap in gaps), gaps
