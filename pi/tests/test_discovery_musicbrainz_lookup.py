"""Tests for nowplaying.discovery.musicbrainz_lookup.

Covers:
  - lookup_by_isrc: walks recordings → first non-bootleg release →
    fetches full release.
  - lookup_by_artist_album: with and without track_count_hint.
  - Multi-pressing resolution picks the closest track count.
  - Misses negative-cache (so retry within TTL no-ops).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _run(coro):
    return asyncio.run(coro)


class _MockResp:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._payload


class _MockSession:
    def __init__(self, responses: list[_MockResp]):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, headers=None):
        return self._responses.pop(0)


def _patch_session_sequence(responses: list):
    """Patch aiohttp.ClientSession in the lookup module to yield the
    given sequence of responses."""
    sessions = []
    factory_calls = {"n": 0}

    def _factory(**kwargs):
        factory_calls["n"] += 1
        # Each ClientSession in the lookup module wraps a single request,
        # so one response per session call.
        if not responses:
            raise AssertionError("ran out of mocked responses")
        sess = _MockSession([responses.pop(0)])
        sessions.append(sess)
        return sess

    return patch(
        "nowplaying.discovery.musicbrainz_lookup.aiohttp.ClientSession",
        side_effect=_factory,
    )


_FULL_RELEASE_PAYLOAD = {
    "title": "Harvest",
    "date": "1972-02-14",
    "artist-credit": [
        {"name": "Neil Young", "joinphrase": ""},
    ],
    "media": [
        {"tracks": [
            {"title": "Out On The Weekend", "length": 271000},
            {"title": "Harvest", "length": 191000},
        ]},
        {"tracks": [
            {"title": "Heart Of Gold", "length": 207000},
            {"title": "Are You Ready For The Country", "length": 201000},
        ]},
    ],
}


def test_lookup_by_isrc_returns_release(monkeypatch, tmp_path):
    """ISRC search finds a recording → picks first non-bootleg release →
    fetches its full media."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    # Isolate negative cache from real DB.
    monkeypatch.setattr(mbl, "_negative_cached", lambda a, b: False)
    monkeypatch.setattr(mbl, "_mark_negative", lambda a, b: None)

    isrc_payload = {
        "recordings": [
            {
                "releases": [
                    {"id": "bootleg-mbid", "status": "Bootleg"},
                    {"id": "good-mbid", "status": "Official"},
                ],
            },
        ],
    }
    responses = [
        _MockResp(200, isrc_payload),
        _MockResp(200, _FULL_RELEASE_PAYLOAD),
    ]
    with _patch_session_sequence(responses):
        out = _run(mbl.lookup_by_isrc("USRE17500095"))

    assert out is not None
    assert out["mbid"] == "good-mbid"
    assert out["artist"] == "Neil Young"
    assert out["album"] == "Harvest"
    assert out["year"] == 1972
    # 4 tracks across 2 media → side A then B
    positions = [t["position"] for t in out["tracks"]]
    assert positions == ["A1", "A2", "B1", "B2"]
    sides = {t["side"] for t in out["tracks"]}
    assert sides == {"A", "B"}
    assert out["tracks"][0]["duration_seconds"] == 271
    assert out["tracks"][2]["title"] == "Heart Of Gold"


def test_lookup_by_isrc_empty_returns_none():
    from nowplaying.discovery import musicbrainz_lookup as mbl
    with _patch_session_sequence([_MockResp(200, {"recordings": []})]):
        out = _run(mbl.lookup_by_isrc("BOGUS"))
    assert out is None


def test_lookup_by_isrc_short_circuits_empty_input():
    from nowplaying.discovery import musicbrainz_lookup as mbl
    assert _run(mbl.lookup_by_isrc("")) is None


def test_lookup_by_artist_album_simple_path(monkeypatch):
    """Without a track-count hint, takes the first fetch_release_mbid hit
    and fetches the full release."""
    from nowplaying.discovery import musicbrainz_lookup as mbl

    monkeypatch.setattr(mbl, "_negative_cached", lambda a, b: False)
    monkeypatch.setattr(mbl, "_mark_negative", lambda a, b: None)

    async def _fake_fetch_mbid(artist, album, timeout_s=15.0):
        return ("found-mbid", "rg-mbid")

    monkeypatch.setattr(
        "nowplaying.coverart.fetch_release_mbid", _fake_fetch_mbid,
    )
    with _patch_session_sequence([_MockResp(200, _FULL_RELEASE_PAYLOAD)]):
        out = _run(mbl.lookup_by_artist_album("Neil Young", "Harvest"))

    assert out is not None
    assert out["mbid"] == "found-mbid"
    assert out["artist"] == "Neil Young"


def test_lookup_by_artist_album_track_count_hint_picks_best(monkeypatch):
    """With a track_count_hint, walk candidates and pick the closest."""
    from nowplaying.discovery import musicbrainz_lookup as mbl

    monkeypatch.setattr(mbl, "_negative_cached", lambda a, b: False)
    monkeypatch.setattr(mbl, "_mark_negative", lambda a, b: None)

    async def _fake_candidates(artist, album, timeout_s=15.0):
        return [
            ("reissue-mbid", None),  # 35 tracks
            ("original-mbid", None),  # 31 tracks (closest to 31 hint)
            ("anniversary-mbid", None),  # 40 tracks
        ]

    async def _fake_recordings(mbid, timeout_s=15.0):
        counts = {
            "reissue-mbid": 35,
            "original-mbid": 31,
            "anniversary-mbid": 40,
        }
        n = counts.get(mbid, 0)
        return [
            {"title": f"T{i}", "duration_seconds": 60}
            for i in range(n)
        ]

    monkeypatch.setattr(
        "nowplaying.coverart.search_release_candidates", _fake_candidates,
    )
    monkeypatch.setattr(
        "nowplaying.coverart.fetch_release_recordings", _fake_recordings,
    )
    # Capture which mbid eventually gets fetched.
    fetched_mbids = []

    async def _fake_release_full(mbid, *, timeout_s):
        fetched_mbids.append(mbid)
        return {
            "mbid": mbid, "artist": "X", "album": "Y", "year": None,
            "tracks": [{"position": "A1", "side": "A",
                        "title": "T", "duration_seconds": 1}],
        }

    monkeypatch.setattr(mbl, "_fetch_release_full", _fake_release_full)
    out = _run(
        mbl.lookup_by_artist_album(
            "X", "Y", track_count_hint=31,
        ),
    )
    assert out is not None
    assert fetched_mbids == ["original-mbid"]


def test_lookup_by_artist_album_miss_stamps_negative_cache(monkeypatch):
    """When MB has no candidates, mark_negative is called so the next
    call within TTL no-ops."""
    from nowplaying.discovery import musicbrainz_lookup as mbl

    monkeypatch.setattr(mbl, "_negative_cached", lambda a, b: False)
    marked = []
    monkeypatch.setattr(
        mbl, "_mark_negative",
        lambda a, b: marked.append((a, b)),
    )

    async def _fake_fetch_mbid(artist, album, timeout_s=15.0):
        return None

    monkeypatch.setattr(
        "nowplaying.coverart.fetch_release_mbid", _fake_fetch_mbid,
    )
    out = _run(mbl.lookup_by_artist_album("Nobody", "Nothing"))
    assert out is None
    assert marked == [("Nobody", "Nothing")]


def test_lookup_by_artist_album_negative_cache_short_circuits(monkeypatch):
    """When negative cache says we've already tried this album, return
    None without hitting the network."""
    from nowplaying.discovery import musicbrainz_lookup as mbl

    monkeypatch.setattr(mbl, "_negative_cached", lambda a, b: True)

    called = {"n": 0}

    async def _should_not_be_called(*a, **k):
        called["n"] += 1
        return None

    monkeypatch.setattr(
        "nowplaying.coverart.fetch_release_mbid", _should_not_be_called,
    )
    out = _run(mbl.lookup_by_artist_album("Cached", "Negative"))
    assert out is None
    assert called["n"] == 0


def test_walk_media_to_tracks_handles_null_lengths():
    from nowplaying.discovery import musicbrainz_lookup as mbl
    media = [
        {"tracks": [
            {"title": "Known", "length": 60000},
            {"title": "Unknown", "length": None},
        ]},
    ]
    out = mbl._walk_media_to_tracks(media)
    assert out == [
        {"position": "A1", "side": "A",
         "title": "Known", "duration_seconds": 60},
        {"position": "A2", "side": "A",
         "title": "Unknown", "duration_seconds": None},
    ]
