"""A release found once for a Shazam (artist, album) must be found again
on the next heartbeat, whatever MusicBrainz calls the artist and album."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
for _p in (_PI_ROOT, _PI_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import recognize_proto  # noqa: E402 — needs the sys.path insert above
from nowplaying.discovery import musicbrainz_lookup as mbl  # noqa: E402 — same
from nowplaying.discovery import schema  # noqa: E402 — same

_SHAZAM_ARTIST = "Various Artists"
_SHAZAM_ALBUM = "The Odyssey (Original Motion Picture Soundtrack)"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def discovered_db(monkeypatch, tmp_path):
    db_path = tmp_path / "discovered.sqlite"
    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    orig_ro, orig_rw = schema.open_ro, schema.open_rw
    monkeypatch.setattr(mbl, "open_ro", lambda p=db_path: orig_ro(p))
    monkeypatch.setattr(mbl, "open_rw", lambda p=db_path: orig_rw(p))
    schema.init_db(db_path)
    return db_path


def _odyssey(mbid="mb-odyssey-1", duration=None) -> dict:
    return {
        "mbid": mbid,
        "artist": "Ludwig Göransson",
        "album": "The Odyssey: Original Motion Picture Soundtrack",
        "year": 2026,
        "tracks": [
            {"position": "A1", "side": "A", "title": "Ithaca",
             "duration_seconds": duration},
            {"position": "A2", "side": "A", "title": "Troy",
             "duration_seconds": 200},
        ],
    }


def _rows(db_path, sql):
    with sqlite3.connect(db_path) as con:
        return con.execute(sql).fetchall()


def test_release_is_found_again_by_the_shazam_key(discovered_db, monkeypatch):
    """Discovery persists under MusicBrainz's names; the next heartbeat
    looks up by Shazam's and must hit."""
    async def _no_isrc(isrc, album, **k):
        return None

    async def _by_artist_album(artist, album, **k):
        return _odyssey()

    monkeypatch.setattr(mbl, "lookup_by_isrc", _no_isrc)
    monkeypatch.setattr(mbl, "lookup_by_artist_album", _by_artist_album)
    _run(recognize_proto._run_discovery(
        _SHAZAM_ARTIST, _SHAZAM_ALBUM, "ISRC1", ("k", "k"),
    ))
    assert mbl.find_discovered_release_by_artist_album(
        _SHAZAM_ARTIST, _SHAZAM_ALBUM,
    ) == "mb-odyssey-1"


def test_second_release_of_the_same_album_reuses_the_first_row(discovered_db):
    """Another Shazam album string resolving to another pressing of an
    album already stored points at the stored row."""
    _run(mbl.persist(_odyssey("mb-odyssey-1"), lookup_key=("VA", "The Odyssey")))
    _run(mbl.persist(
        _odyssey("mb-odyssey-2"),
        lookup_key=(_SHAZAM_ARTIST, _SHAZAM_ALBUM),
    ))
    assert _rows(discovered_db, "SELECT mbid FROM releases") == [("mb-odyssey-1",)]
    assert mbl.find_discovered_release_by_artist_album(
        _SHAZAM_ARTIST, _SHAZAM_ALBUM,
    ) == "mb-odyssey-1"


def test_repersist_keeps_a_filled_duration(discovered_db):
    _run(mbl.persist(_odyssey()))
    assert schema.set_track_duration_mbid("mb-odyssey-1", "A1", 245) == 1
    _run(mbl.persist(_odyssey()))
    assert _rows(
        discovered_db,
        "SELECT position, duration_seconds FROM tracks ORDER BY rowid",
    ) == [("A1", 245), ("A2", 200)]


def test_isrc_release_must_carry_the_shazam_album_title():
    """The first release of an ISRC's recording is often the single; only
    a release titled like the Shazam album may be taken."""
    data = {"recordings": [{"releases": [
        {"id": "single", "status": "Official", "title": "Na Na Na"},
        {"id": "bootleg", "status": "Bootleg", "title": "Danger Days"},
        {"id": "album", "status": "Official",
         "title": "Danger Days: The True Lives of the Fabulous Killjoys"},
    ]}]}
    pick = mbl._pick_release_from_recording_search
    assert pick(
        data, "Danger Days - The True Lives of the Fabulous Killjoys",
    ) == "album"
    assert pick(data, "Some Other Album") is None
