"""A MusicBrainz search that got no answer is not a miss: it raises
``Unreachable`` and the discovery lookup stamps no 7-day negative."""
from __future__ import annotations

import asyncio

import pytest

from nowplaying import coverart
from nowplaying.discovery import musicbrainz_lookup as mbl


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


def _session_answering(status, payload=None):
    class _Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            return _Resp(status, payload)

    return _Session


@pytest.fixture
def marked(monkeypatch):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(coverart.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(coverart, "_negative_cached", lambda a, b: False)
    monkeypatch.setattr(mbl, "_negative_cached", lambda a, b: False)
    out = []
    monkeypatch.setattr(mbl, "_mark_negative", lambda a, b: out.append((a, b)))
    return out


def test_release_search_raises_unreachable_on_503(marked, monkeypatch):
    monkeypatch.setattr(coverart.aiohttp, "ClientSession", _session_answering(503))
    with pytest.raises(coverart.Unreachable):
        asyncio.run(coverart.fetch_release_mbid("Neil Young", "Harvest"))


def test_candidate_search_raises_unreachable_on_503(marked, monkeypatch):
    monkeypatch.setattr(coverart.aiohttp, "ClientSession", _session_answering(503))
    with pytest.raises(coverart.Unreachable):
        asyncio.run(coverart.search_release_candidates("Neil Young", "Harvest"))


def test_empty_search_result_is_still_a_miss(marked, monkeypatch):
    monkeypatch.setattr(
        coverart.aiohttp, "ClientSession",
        _session_answering(200, {"releases": []}),
    )
    assert asyncio.run(coverart.fetch_release_mbid("Neil Young", "Harvest")) is None
    assert asyncio.run(
        coverart.search_release_candidates("Neil Young", "Harvest"),
    ) == []


@pytest.mark.parametrize("hint", [None, 10])
def test_lookup_stamps_no_negative_when_search_is_unreachable(
    marked, monkeypatch, hint,
):
    monkeypatch.setattr(coverart.aiohttp, "ClientSession", _session_answering(503))
    out = asyncio.run(
        mbl.lookup_by_artist_album("Neil Young", "Harvest", track_count_hint=hint),
    )
    assert out is None
    assert marked == []


def test_lookup_stamps_no_negative_when_no_candidate_could_be_fetched(
    marked, monkeypatch,
):
    async def _candidates(artist, album, **kw):
        return [("mb-1", None), ("mb-2", None)]

    async def _recordings(mbid, **kw):
        return None  # could not reach MusicBrainz

    monkeypatch.setattr(coverart, "search_release_candidates", _candidates)
    monkeypatch.setattr(coverart, "fetch_release_recordings", _recordings)
    out = asyncio.run(
        mbl.lookup_by_artist_album("Neil Young", "Harvest", track_count_hint=10),
    )
    assert out is None
    assert marked == []
