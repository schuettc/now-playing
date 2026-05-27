"""Tests for nowplaying.discovery.musicbrainz_lookup.persist.

Persist writes idempotently into discovered.sqlite — same MBID twice
produces one releases row, and the tracks rows are replaced wholesale
on re-persist.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def discovered_db(monkeypatch, tmp_path):
    """Point the discovery module at a fresh sqlite file under tmp_path."""
    from nowplaying.discovery import schema
    from nowplaying.discovery import musicbrainz_lookup as mbl
    db_path = tmp_path / "discovered.sqlite"
    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(mbl, "DISCOVERED_DB_PATH", db_path)
    # The schema helpers default-arg to DISCOVERED_DB_PATH at *call time*
    # only via module-default; ensure init_db uses the patched path.
    monkeypatch.setattr(
        "nowplaying.discovery.schema.DISCOVERED_DB_PATH", db_path,
    )
    # Re-bind open_ro/open_rw default arg too — they capture the default
    # at function-def time, so we need to wrap them.
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
    # Initialize schema in the temp file.
    schema.init_db(db_path)
    return db_path


def _release(mbid="mb-1", tracks=None) -> dict:
    return {
        "mbid": mbid,
        "artist": "Neil Young",
        "album": "Harvest",
        "year": 1972,
        "tracks": tracks or [
            {"position": "A1", "side": "A",
             "title": "Out On The Weekend", "duration_seconds": 271},
            {"position": "A2", "side": "A",
             "title": "Harvest", "duration_seconds": 191},
        ],
    }


def _query_rows(db_path):
    import sqlite3
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        releases = [dict(r) for r in con.execute("SELECT * FROM releases")]
        tracks = [dict(r) for r in con.execute(
            "SELECT * FROM tracks ORDER BY rowid")]
    return releases, tracks


def test_persist_writes_release_and_tracks(discovered_db):
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist(_release()))
    releases, tracks = _query_rows(discovered_db)
    assert len(releases) == 1
    assert releases[0]["mbid"] == "mb-1"
    assert releases[0]["artist"] == "Neil Young"
    assert releases[0]["title"] == "Harvest"
    assert releases[0]["year"] == 1972
    assert releases[0]["discovered_at"] > 0
    assert len(tracks) == 2
    assert tracks[0]["position"] == "A1"
    assert tracks[1]["duration_seconds"] == 191


def test_persist_idempotent_on_replay(discovered_db):
    """Same MBID twice → one releases row."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist(_release()))
    _run(mbl.persist(_release()))
    releases, tracks = _query_rows(discovered_db)
    assert len(releases) == 1
    assert len(tracks) == 2  # not 4 — DELETE+INSERT


def test_persist_replaces_tracks_on_update(discovered_db):
    """If MB returns a different tracklist later, the old tracks are
    deleted and the new ones inserted."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist(_release()))
    new_tracks = [
        {"position": "A1", "side": "A",
         "title": "Out On The Weekend (Remastered)", "duration_seconds": 272},
    ]
    _run(mbl.persist(_release(tracks=new_tracks)))
    releases, tracks = _query_rows(discovered_db)
    assert len(releases) == 1
    assert len(tracks) == 1
    assert tracks[0]["title"] == "Out On The Weekend (Remastered)"
    assert tracks[0]["duration_seconds"] == 272


def test_persist_no_mbid_is_noop(discovered_db):
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist({"mbid": "", "tracks": []}))
    releases, _ = _query_rows(discovered_db)
    assert releases == []


def test_find_discovered_release_by_artist_album_hits(discovered_db):
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist(_release()))
    mbid = mbl.find_discovered_release_by_artist_album(
        "Neil Young", "Harvest",
    )
    assert mbid == "mb-1"


def test_find_discovered_release_case_insensitive(discovered_db):
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist(_release()))
    assert mbl.find_discovered_release_by_artist_album(
        "neil young", "HARVEST",
    ) == "mb-1"


def test_find_discovered_release_misses_returns_none(discovered_db):
    from nowplaying.discovery import musicbrainz_lookup as mbl
    assert mbl.find_discovered_release_by_artist_album(
        "Nobody", "Nothing",
    ) is None


def test_persist_writes_normalized_album(discovered_db):
    """Persisting an MB release writes the lower/trim title into
    ``normalized_album`` so the edition-aware lookup works."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist(_release()))
    releases, _ = _query_rows(discovered_db)
    assert len(releases) == 1
    assert releases[0]["normalized_album"] == "harvest"


def test_find_discovered_release_resolves_edition_suffix(discovered_db):
    """Bug 2 regression: Shazam returns
    ``"Brothers (Deluxe Remastered Anniversary Edition)"``, but MB
    persisted the canonical ``"Brothers"``. The lookup must resolve
    them to the same MBID."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    brothers_mbid = "df642560-e127-44ba-8144-8faa60fe9979"
    _run(mbl.persist({
        "mbid": brothers_mbid,
        "artist": "The Black Keys",
        "album": "Brothers",
        "year": 2010,
        "tracks": [
            {"position": "A1", "side": "A",
             "title": "Everlasting Light", "duration_seconds": 203},
        ],
    }))
    # Shazam-shaped edition string must resolve to the canonical row.
    assert mbl.find_discovered_release_by_artist_album(
        "The Black Keys",
        "Brothers (Deluxe Remastered Anniversary Edition)",
    ) == brothers_mbid
    # The canonical name also still works.
    assert mbl.find_discovered_release_by_artist_album(
        "The Black Keys", "Brothers",
    ) == brothers_mbid


def test_find_discovered_release_does_not_strip_live_marker(discovered_db):
    """``"Mama Said Knock You Out (Live)"`` is a distinct release from
    ``"Mama Said Knock You Out"``; the normalizer must NOT collapse them."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist({
        "mbid": "studio-mbid",
        "artist": "LL Cool J",
        "album": "Mama Said Knock You Out",
        "year": 1990,
        "tracks": [{"position": "A1", "side": "A", "title": "x",
                    "duration_seconds": 200}],
    }))
    # Live edition should NOT match the studio row.
    assert mbl.find_discovered_release_by_artist_album(
        "LL Cool J", "Mama Said Knock You Out (Live)",
    ) is None


def test_init_db_backfills_normalized_album(tmp_path, monkeypatch):
    """init_db's migration must backfill normalized_album for legacy
    rows that predate the column. Simulates the live Pi state: a
    pre-migration discovered.sqlite with a Brothers row, then init_db
    runs at boot and the lookup starts working."""
    import sqlite3
    from nowplaying.discovery import schema
    db_path = tmp_path / "legacy.sqlite"
    # Build a "legacy" DB without the normalized_album column.
    with sqlite3.connect(db_path) as con:
        con.execute(
            "CREATE TABLE releases ("
            "mbid TEXT PRIMARY KEY, artist TEXT, title TEXT, year INTEGER, "
            "art_url TEXT, discogs_release_id INTEGER, discovered_at INTEGER)"
        )
        con.execute(
            "INSERT INTO releases (mbid, artist, title, year, "
            "discovered_at) VALUES (?, ?, ?, ?, ?)",
            ("legacy-mbid", "The Black Keys", "Brothers", 2010, 1),
        )
        con.commit()
    # Run init_db — migration adds column + backfills.
    schema.init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        cols = {r[1] for r in con.execute("PRAGMA table_info(releases)")}
        assert "normalized_album" in cols
        row = con.execute(
            "SELECT normalized_album FROM releases WHERE mbid = ?",
            ("legacy-mbid",),
        ).fetchone()
        assert row["normalized_album"] == "brothers"


def test_init_db_migration_idempotent(tmp_path):
    """Re-running init_db on an already-migrated DB doesn't error or
    duplicate the column."""
    from nowplaying.discovery import schema
    db_path = tmp_path / "fresh.sqlite"
    schema.init_db(db_path)
    schema.init_db(db_path)  # second call must be a no-op.
    schema.init_db(db_path)  # third for good measure.


def test_negative_cache_round_trip(discovered_db):
    from nowplaying.discovery import musicbrainz_lookup as mbl
    assert mbl._negative_cached("X", "Y") is False
    mbl._mark_negative("X", "Y")
    assert mbl._negative_cached("X", "Y") is True
    # Different (artist, album) still uncached.
    assert mbl._negative_cached("X", "Z") is False
