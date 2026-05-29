"""Tests for scripts.clean_titles_backfill."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _make_discogs_db(path: Path) -> sqlite3.Connection:
    """Open a tmp Discogs DB with the full schema + migrations applied."""
    from scripts.discogs import _db

    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    return con


def test_backfill_null_clean_title(tmp_path):
    """Rows with clean_title IS NULL get filled; returns count."""
    db = tmp_path / "discogs.sqlite"
    con = _make_discogs_db(db)
    # Insert a minimal release and a track with a NULL clean_title.
    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'The Beatles', 'Magical Mystery Tour')"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, clean_title, clean_title_source)"
        " VALUES (1, 'A1', 'Penny Lane (2017 Mix)', NULL, NULL)"
    )
    con.close()

    from scripts import clean_titles_backfill as bf

    n = asyncio.run(bf.backfill_db(db, llm=None))
    assert n == 1

    con2 = sqlite3.connect(db)
    row = con2.execute(
        "SELECT clean_title, clean_title_source FROM tracks WHERE position = 'A1'"
    ).fetchone()
    con2.close()
    assert row[0] == "Penny Lane"
    assert row[1] == "regex"


def test_backfill_skips_already_cleaned(tmp_path):
    """Rows that already have clean_title are NOT touched in default mode."""
    db = tmp_path / "discogs.sqlite"
    con = _make_discogs_db(db)
    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'The Beatles', 'Magical Mystery Tour')"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, clean_title, clean_title_source)"
        " VALUES (1, 'A1', 'Penny Lane (2017 Mix)', 'Penny Lane', 'regex')"
    )
    con.close()

    from scripts import clean_titles_backfill as bf

    n = asyncio.run(bf.backfill_db(db, llm=None))
    assert n == 0


def test_reclean_regex_updates_regex_rows(tmp_path):
    """--reclean-regex re-cleans rows with source='regex'; leaves 'llm' rows alone."""
    db = tmp_path / "discogs.sqlite"
    con = _make_discogs_db(db)
    con.execute(
        "INSERT INTO releases (id, artist, title) VALUES (1, 'Various', 'Hits')"
    )
    # Row 1: source='regex' — should be re-cleaned.
    con.execute(
        "INSERT INTO tracks (release_id, position, title, clean_title, clean_title_source)"
        " VALUES (1, 'A1', 'Hey Jude (Remastered)', 'Hey Jude (Remastered)', 'regex')"
    )
    # Row 2: source='llm' — must be left untouched.
    con.execute(
        "INSERT INTO tracks (release_id, position, title, clean_title, clean_title_source)"
        " VALUES (1, 'A2', 'Let It Be (2021 Mix)', 'Let It Be', 'llm')"
    )
    con.close()

    from scripts import clean_titles_backfill as bf

    n = asyncio.run(bf.backfill_db(db, llm=None, reclean_regex=True))
    assert n == 1  # only the regex row updated

    con2 = sqlite3.connect(db)
    rows = {
        r[0]: (r[1], r[2])
        for r in con2.execute(
            "SELECT position, clean_title, clean_title_source FROM tracks ORDER BY position"
        )
    }
    con2.close()
    # regex row got re-cleaned to 'Hey Jude'.
    assert rows["A1"][0] == "Hey Jude"
    assert rows["A1"][1] == "regex"
    # llm row is untouched.
    assert rows["A2"][0] == "Let It Be"
    assert rows["A2"][1] == "llm"


def test_load_env_reads_key(tmp_path, monkeypatch):
    """_load_env populates ANTHROPIC_API_KEY from the given .env file."""
    from scripts import clean_titles_backfill as bf

    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=test-key-123\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    bf._load_env(env)
    assert os.environ.get("ANTHROPIC_API_KEY") == "test-key-123"
