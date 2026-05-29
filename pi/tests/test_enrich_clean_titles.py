"""Tests for clean_release_titles in scripts.discogs._enrich."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _open_db_at(path: Path) -> sqlite3.Connection:
    """Open a Discogs DB at an arbitrary path using the real DDL + migrations."""
    from scripts.discogs import _db

    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    return con


def test_clean_release_titles_populates_column(tmp_path):
    db = tmp_path / "discogs.sqlite"
    con = _open_db_at(db)

    # Insert a release and a track with a NULL clean_title.
    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'The Beatles', 'Magical Mystery Tour')"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, clean_title) "
        "VALUES (1, 'A2', 'Penny Lane (2017 Mix)', NULL)"
    )

    from scripts.discogs import _enrich

    asyncio.run(_enrich.clean_release_titles(con, release_id=1, llm=None))

    row = con.execute(
        "SELECT clean_title, clean_title_source FROM tracks "
        "WHERE release_id=1 AND position='A2'"
    ).fetchone()
    assert row[0] == "Penny Lane"
    assert row[1] == "regex"


def test_clean_release_titles_skips_already_populated(tmp_path):
    """Tracks that already have clean_title set must not be re-written."""
    db = tmp_path / "discogs.sqlite"
    con = _open_db_at(db)

    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'The Beatles', 'Abbey Road')"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, clean_title, clean_title_source) "
        "VALUES (1, 'A1', 'Come Together', 'Come Together', 'regex')"
    )

    from scripts.discogs import _enrich

    updated = asyncio.run(_enrich.clean_release_titles(con, release_id=1, llm=None))
    assert updated == 0

    row = con.execute(
        "SELECT clean_title, clean_title_source FROM tracks "
        "WHERE release_id=1 AND position='A1'"
    ).fetchone()
    # Original value preserved.
    assert row[0] == "Come Together"
    assert row[1] == "regex"


def test_clean_release_titles_returns_updated_count(tmp_path):
    """Return value equals the number of rows updated."""
    db = tmp_path / "discogs.sqlite"
    con = _open_db_at(db)

    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'Led Zeppelin', 'IV')"
    )
    for pos, title in [("A1", "Black Dog (2014 Remaster)"), ("A2", "Rock and Roll (2014 Remaster)")]:
        con.execute(
            "INSERT INTO tracks (release_id, position, title, clean_title) VALUES (1, ?, ?, NULL)",
            (pos, title),
        )

    from scripts.discogs import _enrich

    updated = asyncio.run(_enrich.clean_release_titles(con, release_id=1, llm=None))
    assert updated == 2
