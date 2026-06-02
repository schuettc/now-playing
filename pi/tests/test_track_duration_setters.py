"""Tests for guarded per-track duration setters in discogs and discovered catalogs."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _open_discogs_db_at(path: Path) -> sqlite3.Connection:
    """Open a Discogs DB at an arbitrary path using the same DDL + migration
    as scripts.discogs._db.open_db, without the hardcoded DATA_DIR."""
    from scripts.discogs import _db

    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    return con


# ---------------------------------------------------------------------------
# Discogs setter tests
# ---------------------------------------------------------------------------


def test_discogs_set_track_duration_null_row(tmp_path, monkeypatch):
    """NULL duration row: setter returns 1 and value becomes 163."""
    db = tmp_path / "discogs.sqlite"
    con = _open_discogs_db_at(db)
    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'Artist', 'Album')"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, duration_seconds) "
        "VALUES (1, 'A1', 'Track One', NULL)"
    )
    con.close()

    import nowplaying.discogs.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "DB_PATH", db)

    rows_updated = catalog_mod.set_track_duration(1, "A1", 163)

    assert rows_updated == 1
    with sqlite3.connect(db) as verify:
        row = verify.execute(
            "SELECT duration_seconds FROM tracks WHERE release_id = 1 AND position = 'A1'"
        ).fetchone()
    assert row[0] == 163


def test_discogs_set_track_duration_guard(tmp_path, monkeypatch):
    """Row already has duration 200: setter returns 0 and value stays 200."""
    db = tmp_path / "discogs.sqlite"
    con = _open_discogs_db_at(db)
    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'Artist', 'Album')"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, duration_seconds) "
        "VALUES (1, 'A1', 'Track One', 200)"
    )
    con.close()

    import nowplaying.discogs.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "DB_PATH", db)

    rows_updated = catalog_mod.set_track_duration(1, "A1", 163)

    assert rows_updated == 0
    with sqlite3.connect(db) as verify:
        row = verify.execute(
            "SELECT duration_seconds FROM tracks WHERE release_id = 1 AND position = 'A1'"
        ).fetchone()
    assert row[0] == 200


# ---------------------------------------------------------------------------
# Discovered setter tests
# ---------------------------------------------------------------------------


def test_discovered_set_track_duration_null_row(tmp_path, monkeypatch):
    """NULL duration row in discovered.sqlite: setter returns 1, value becomes 163."""
    db = tmp_path / "discovered.sqlite"
    from nowplaying.discovery import schema

    schema.init_db(db)

    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO releases (mbid, artist, title) "
            "VALUES ('abc-123', 'Artist', 'Album')"
        )
        con.execute(
            "INSERT INTO tracks (mbid, position, title, duration_seconds) "
            "VALUES ('abc-123', 'A1', 'Track One', NULL)"
        )

    import nowplaying.discovery as discovery_mod
    import nowplaying.discovery.schema as schema_mod

    monkeypatch.setattr(schema_mod, "DISCOVERED_DB_PATH", db)

    rows_updated = discovery_mod.set_track_duration_mbid("abc-123", "A1", 163)

    assert rows_updated == 1
    with sqlite3.connect(db) as verify:
        row = verify.execute(
            "SELECT duration_seconds FROM tracks WHERE mbid = 'abc-123' AND position = 'A1'"
        ).fetchone()
    assert row[0] == 163


def test_discovered_set_track_duration_guard(tmp_path, monkeypatch):
    """Row already has duration 200 in discovered.sqlite: returns 0, value stays 200."""
    db = tmp_path / "discovered.sqlite"
    from nowplaying.discovery import schema

    schema.init_db(db)

    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO releases (mbid, artist, title) "
            "VALUES ('abc-123', 'Artist', 'Album')"
        )
        con.execute(
            "INSERT INTO tracks (mbid, position, title, duration_seconds) "
            "VALUES ('abc-123', 'A1', 'Track One', 200)"
        )

    import nowplaying.discovery as discovery_mod
    import nowplaying.discovery.schema as schema_mod

    monkeypatch.setattr(schema_mod, "DISCOVERED_DB_PATH", db)

    rows_updated = discovery_mod.set_track_duration_mbid("abc-123", "A1", 163)

    assert rows_updated == 0
    with sqlite3.connect(db) as verify:
        row = verify.execute(
            "SELECT duration_seconds FROM tracks WHERE mbid = 'abc-123' AND position = 'A1'"
        ).fetchone()
    assert row[0] == 200
