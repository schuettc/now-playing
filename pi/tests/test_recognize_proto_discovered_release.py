"""Tests for the discovered-release path in recognize_proto.recognize.

Shazam hit + Discogs miss + discovered.sqlite hit → payload carries
release_mbid + tracklist.

Shazam hit + Discogs miss + discovered miss → schedules a background
discovery task (mocked).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_SCRIPTS = _PI_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


import recognize_proto  # noqa: E402


_SHAZAM = {
    "title": "Heart Of Gold",
    "subtitle": "Neil Young",
    "artist": "Neil Young",
    "isrc": "USRE17500095",
    "album": "Harvest",
    "art_url": "https://is2-ssl.mzstatic.com/image/coverarthq.jpg",
    "albumadamid": "203708420",
    "raw": {},
    "match_offset_s": 12.5,
}


def _run(clip_path: Path) -> dict:
    return asyncio.run(recognize_proto.recognize(clip_path))


def test_discovered_hit_attaches_release_mbid_and_tracklist():
    """When discovered.sqlite has a release for (artist, album), the
    payload carries release_mbid + tracklist."""
    catalog_release = {
        "mbid": "harvest-mb-1",
        "artist": "Neil Young",
        "title": "Harvest",
        "tracks": [
            {"position": "A1", "side": "A",
             "title": "Out On The Weekend", "duration_seconds": 271},
            {"position": "A2", "side": "A",
             "title": "Harvest", "duration_seconds": 191},
        ],
    }
    with mock.patch.object(
        recognize_proto.shazam, "identify",
        new=mock.AsyncMock(return_value=_SHAZAM),
    ), mock.patch.object(
        recognize_proto.discogs_catalog, "find_by_artist_title",
        return_value=None,
    ), mock.patch.object(
        recognize_proto.musicbrainz_lookup,
        "find_discovered_release_by_artist_album",
        return_value="harvest-mb-1",
    ), mock.patch(
        "nowplaying.catalog.get_release", return_value=catalog_release,
    ):
        out = _run(Path("/tmp/fake.wav"))

    assert out["release_mbid"] == "harvest-mb-1"
    assert "tracklist" in out
    assert len(out["tracklist"]) == 2
    assert out["tracklist"][0]["position"] == "A1"
    assert out["tracklist"][0]["duration_seconds"] == 271
    assert out["album"] == "Harvest"
    assert out["art_url"] == _SHAZAM["art_url"]


def test_discovered_miss_schedules_background_task():
    """Discovered.sqlite has no row → background discovery task fires."""
    with mock.patch.object(
        recognize_proto.shazam, "identify",
        new=mock.AsyncMock(return_value=_SHAZAM),
    ), mock.patch.object(
        recognize_proto.discogs_catalog, "find_by_artist_title",
        return_value=None,
    ), mock.patch.object(
        recognize_proto.musicbrainz_lookup,
        "find_discovered_release_by_artist_album",
        return_value=None,
    ), mock.patch.object(
        recognize_proto, "_schedule_discovery",
    ) as mock_schedule:
        out = _run(Path("/tmp/fake.wav"))

    # No release_mbid attached on first heartbeat.
    assert "release_mbid" not in out
    # But slice-1 enrichment is still there.
    assert out["album"] == "Harvest"
    assert out["art_url"] == _SHAZAM["art_url"]
    # And the background task was scheduled.
    mock_schedule.assert_called_once()
    arg = mock_schedule.call_args.args[0]
    assert arg["album"] == "Harvest"
    assert arg["isrc"] == "USRE17500095"


def test_discogs_hit_skips_discovery_path():
    """Discogs hit should never fall through to discovery — Discogs is
    canonical."""
    rel = {
        "id": 42, "artist": "Neil Young", "title": "Harvest",
        "year": 1972, "label": "Reprise", "catno": "MS 2032",
        "art_path": "/tmp/42.jpg", "tracks": [],
        "matched_track_title": "Heart Of Gold",
        "matched_track_position": "A3", "match_score": 0.95,
    }
    with mock.patch.object(
        recognize_proto.shazam, "identify",
        new=mock.AsyncMock(return_value=_SHAZAM),
    ), mock.patch.object(
        recognize_proto.discogs_catalog, "find_by_artist_title",
        return_value=rel,
    ), mock.patch.object(
        recognize_proto.asyncio, "create_task",
    ), mock.patch.object(
        recognize_proto.musicbrainz_lookup,
        "find_discovered_release_by_artist_album",
    ) as mock_find, mock.patch.object(
        recognize_proto, "_schedule_discovery",
    ) as mock_schedule:
        out = _run(Path("/tmp/fake.wav"))

    assert out["release_id"] == 42
    # Discovery path must not be consulted when Discogs wins.
    mock_find.assert_not_called()
    mock_schedule.assert_not_called()


def test_schedule_discovery_dedups_in_flight(monkeypatch):
    """Two back-to-back calls for the same (artist, album) → only one
    asyncio task spawned."""
    # Clear in-flight tracker.
    recognize_proto._in_flight_discovery.clear()

    spawned = []

    def _fake_create_task(coro):
        # Track call but immediately close the coroutine so it doesn't
        # spawn (we're not in an event loop here).
        coro.close()
        task = mock.MagicMock()
        task.done.return_value = False
        spawned.append(task)
        return task

    fake_loop = mock.MagicMock()
    fake_loop.create_task.side_effect = _fake_create_task
    monkeypatch.setattr(
        recognize_proto.asyncio, "get_running_loop",
        lambda: fake_loop,
    )

    recognize_proto._schedule_discovery(_SHAZAM)
    recognize_proto._schedule_discovery(_SHAZAM)
    recognize_proto._schedule_discovery(_SHAZAM)
    assert len(spawned) == 1, (
        "duplicate schedule_discovery calls for same (artist, album) "
        "must collapse to one task"
    )


def test_schedule_discovery_noop_on_empty_artist():
    recognize_proto._in_flight_discovery.clear()
    # Should not raise even though no event loop is running.
    recognize_proto._schedule_discovery({
        "subtitle": "", "album": "X", "isrc": "",
    })
    assert recognize_proto._in_flight_discovery == {}


def test_run_discovery_persists_on_isrc_hit(monkeypatch):
    """If lookup_by_isrc returns a release, persist runs and we log a
    success line."""
    persisted: list[dict] = []

    async def _fake_isrc(isrc, **k):
        return {
            "mbid": "mb-1", "artist": "A", "album": "B",
            "year": 2000, "tracks": [],
        }

    async def _fake_persist(rel, *, llm=None):
        persisted.append(rel)

    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup,
        "lookup_by_isrc", _fake_isrc,
    )
    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup,
        "persist", _fake_persist,
    )

    asyncio.run(
        recognize_proto._run_discovery("A", "B", "ISRC", ("a", "b")),
    )
    assert len(persisted) == 1
    assert persisted[0]["mbid"] == "mb-1"


def test_run_discovery_falls_back_to_artist_album(monkeypatch):
    """ISRC miss → tries lookup_by_artist_album."""
    called = {"isrc": 0, "aa": 0}
    persisted: list[dict] = []

    async def _fake_isrc(isrc, **k):
        called["isrc"] += 1
        return None

    async def _fake_aa(artist, album, **k):
        called["aa"] += 1
        return {
            "mbid": "mb-2", "artist": artist,
            "album": album, "tracks": [],
        }

    async def _fake_persist(rel, *, llm=None):
        persisted.append(rel)

    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup, "lookup_by_isrc", _fake_isrc,
    )
    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup,
        "lookup_by_artist_album", _fake_aa,
    )
    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup, "persist", _fake_persist,
    )

    asyncio.run(
        recognize_proto._run_discovery("A", "B", "ISRC", ("a", "b")),
    )
    assert called == {"isrc": 1, "aa": 1}
    assert persisted[0]["mbid"] == "mb-2"


def test_brothers_edition_string_resolves_without_re_scheduling(
    discovered_db_for_recognize,  # noqa: ARG001 — fixture sets up persisted row
):
    """Live-bug regression (2026-05-27): Shazam returns
    ``"Brothers (Deluxe Remastered Anniversary Edition)"``,
    discovered.sqlite already has the canonical ``"Brothers"`` row.
    The recognize cascade must attach release_mbid + tracklist AND
    NOT re-schedule discovery (the heartbeat-spam fix).
    """
    shazam_payload = {
        "title": "Ten Cent Pistol",
        "subtitle": "The Black Keys",
        "artist": "The Black Keys",
        "isrc": "USKL21090007",
        "album": "Brothers (Deluxe Remastered Anniversary Edition)",
        "art_url": "https://is2-ssl.mzstatic.com/image/coverarthq.jpg",
        "albumadamid": "1486889560",
        "raw": {},
        "match_offset_s": 12.5,
    }
    brothers_mbid = "df642560-e127-44ba-8144-8faa60fe9979"
    catalog_release = {
        "mbid": brothers_mbid,
        "artist": "The Black Keys",
        "title": "Brothers",
        "tracks": [
            {"position": "A1", "side": "A",
             "title": "Everlasting Light", "duration_seconds": 203},
            {"position": "A2", "side": "A",
             "title": "Next Girl", "duration_seconds": 193},
        ],
    }
    with mock.patch.object(
        recognize_proto.shazam, "identify",
        new=mock.AsyncMock(return_value=shazam_payload),
    ), mock.patch.object(
        recognize_proto.discogs_catalog, "find_by_artist_title",
        return_value=None,
    ), mock.patch(
        "nowplaying.catalog.get_release", return_value=catalog_release,
    ), mock.patch.object(
        recognize_proto, "_schedule_discovery",
    ) as mock_schedule:
        out = _run(Path("/tmp/fake.wav"))

    assert out["release_mbid"] == brothers_mbid, (
        "edition-suffixed Shazam album must resolve to canonical MB row"
    )
    assert len(out["tracklist"]) == 2
    # The critical assertion: discovery MUST NOT be re-scheduled. This
    # is bug 3 — once bug 2 is fixed, the short-circuit kicks in and the
    # heartbeat-spam stops.
    mock_schedule.assert_not_called()


@pytest.fixture
def discovered_db_for_recognize(monkeypatch, tmp_path):
    """Persist the Black Keys *Brothers* canonical row into a fresh
    discovered.sqlite that recognize_proto's lookup will read from."""
    import asyncio as _asyncio
    from nowplaying.discovery import schema
    from nowplaying.discovery import musicbrainz_lookup as mbl
    db_path = tmp_path / "discovered.sqlite"
    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(mbl, "DISCOVERED_DB_PATH", db_path)
    orig_open_ro = schema.open_ro
    orig_open_rw = schema.open_rw
    monkeypatch.setattr(schema, "open_ro", lambda p=db_path: orig_open_ro(p))
    monkeypatch.setattr(schema, "open_rw", lambda p=db_path: orig_open_rw(p))
    monkeypatch.setattr(mbl, "open_ro", lambda p=db_path: orig_open_ro(p))
    monkeypatch.setattr(mbl, "open_rw", lambda p=db_path: orig_open_rw(p))
    schema.init_db(db_path)
    _asyncio.run(mbl.persist({
        "mbid": "df642560-e127-44ba-8144-8faa60fe9979",
        "artist": "The Black Keys",
        "album": "Brothers",
        "year": 2010,
        "tracks": [
            {"position": "A1", "side": "A",
             "title": "Everlasting Light", "duration_seconds": 203},
            {"position": "A2", "side": "A",
             "title": "Next Girl", "duration_seconds": 193},
        ],
    }))
    return db_path


def test_run_discovery_swallows_exceptions(monkeypatch):
    """Background task must never raise out — caller is asyncio.create_task."""
    async def _boom(*a, **k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup, "lookup_by_isrc", _boom,
    )
    # Must not raise.
    asyncio.run(
        recognize_proto._run_discovery("A", "B", "ISRC", ("a", "b")),
    )
