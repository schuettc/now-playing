"""Tests for tracks.clean_title / clean_title_source schema migration."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _open_db_at(path: Path) -> sqlite3.Connection:
    """Open a Discogs DB at an arbitrary path using the same DDL + migration
    as scripts.discogs._db.open_db, but without the hardcoded DATA_DIR so
    tests can use tmp_path without touching the real database."""
    from scripts.discogs import _db

    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    return con


def test_tracks_has_clean_title_columns(tmp_path):
    db = tmp_path / "discogs.sqlite"
    con = _open_db_at(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
    assert "clean_title" in cols
    assert "clean_title_source" in cols


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "discogs.sqlite"
    con = _open_db_at(db)
    con.close()
    con2 = _open_db_at(db)  # second open must not raise
    cols = {r[1] for r in con2.execute("PRAGMA table_info(tracks)")}
    assert "clean_title" in cols
