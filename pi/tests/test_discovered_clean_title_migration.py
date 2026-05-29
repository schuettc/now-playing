from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def test_discovered_tracks_has_clean_title(tmp_path):
    from nowplaying.discovery import schema
    db = tmp_path / "discovered.sqlite"
    schema.init_db(db)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
    assert "clean_title" in cols
    assert "clean_title_source" in cols


def test_discovered_migration_idempotent(tmp_path):
    from nowplaying.discovery import schema
    db = tmp_path / "discovered.sqlite"
    schema.init_db(db)
    schema.init_db(db)  # second init must not raise
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
    assert "clean_title" in cols
