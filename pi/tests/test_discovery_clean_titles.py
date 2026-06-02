"""Tests for clean_title population during discovered-release persist.

When a track like "Penny Lane (2017 Mix)" is written via
``musicbrainz_lookup.persist``, the ``clean_title`` and
``clean_title_source`` columns must be populated at write time.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _run(coro):
    return asyncio.run(coro)


def _make_discovered_db(monkeypatch, tmp_path):
    """Point the discovery module at a fresh sqlite file under tmp_path."""
    from nowplaying.discovery import schema
    from nowplaying.discovery import musicbrainz_lookup as mbl
    db_path = tmp_path / "discovered.sqlite"
    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(mbl, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(
        "nowplaying.discovery.schema.DISCOVERED_DB_PATH", db_path,
    )
    orig_open_ro = schema.open_ro
    orig_open_rw = schema.open_rw

    def _ro(p=db_path):
        return orig_open_ro(p)

    def _rw(p=db_path):
        return orig_open_rw(p)

    monkeypatch.setattr(schema, "open_ro", _ro)
    monkeypatch.setattr(schema, "open_rw", _rw)
    monkeypatch.setattr(mbl, "open_ro", _ro)
    monkeypatch.setattr(mbl, "open_rw", _rw)
    schema.init_db(db_path)
    return db_path


def _query_tracks(db_path):
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM tracks ORDER BY rowid"
        )]


def test_persist_cleans_title_regex_path(monkeypatch, tmp_path):
    """persist uses the regex cleaner.

    "Penny Lane (2017 Mix)" → clean_title="Penny Lane", source="regex".
    """
    db_path = _make_discovered_db(monkeypatch, tmp_path)
    from nowplaying.discovery import musicbrainz_lookup as mbl

    release = {
        "mbid": "mb-penny",
        "artist": "The Beatles",
        "album": "Magical Mystery Tour",
        "year": 1967,
        "tracks": [
            {
                "position": "A1",
                "side": "A",
                "title": "Penny Lane (2017 Mix)",
                "duration_seconds": 180,
            },
        ],
    }
    _run(mbl.persist(release))

    tracks = _query_tracks(db_path)
    assert len(tracks) == 1
    assert tracks[0]["clean_title"] == "Penny Lane"
    assert tracks[0]["clean_title_source"] == "regex"


def test_persist_keeps_raw_title_unchanged(monkeypatch, tmp_path):
    """The raw ``title`` column is unmodified; only clean_title is derived."""
    db_path = _make_discovered_db(monkeypatch, tmp_path)
    from nowplaying.discovery import musicbrainz_lookup as mbl

    release = {
        "mbid": "mb-raw",
        "artist": "The Beatles",
        "album": "Magical Mystery Tour",
        "year": 1967,
        "tracks": [
            {
                "position": "A1",
                "side": "A",
                "title": "Penny Lane (2017 Mix)",
                "duration_seconds": 180,
            },
        ],
    }
    _run(mbl.persist(release))

    tracks = _query_tracks(db_path)
    assert tracks[0]["title"] == "Penny Lane (2017 Mix)"


def test_persist_title_without_annotation_stays_same(monkeypatch, tmp_path):
    """A title with no strippable annotation: clean_title == raw title."""
    db_path = _make_discovered_db(monkeypatch, tmp_path)
    from nowplaying.discovery import musicbrainz_lookup as mbl

    release = {
        "mbid": "mb-clean",
        "artist": "The Beatles",
        "album": "Abbey Road",
        "year": 1969,
        "tracks": [
            {
                "position": "B1",
                "side": "B",
                "title": "Here Comes the Sun",
                "duration_seconds": 185,
            },
        ],
    }
    _run(mbl.persist(release))

    tracks = _query_tracks(db_path)
    assert tracks[0]["clean_title"] == "Here Comes the Sun"
    assert tracks[0]["clean_title_source"] == "regex"


