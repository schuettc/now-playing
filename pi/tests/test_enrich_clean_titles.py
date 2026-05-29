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


def test_duration_match_uses_clean_title():
    from scripts.discogs import _enrich
    import sqlite3
    mb_by_title = {_enrich._norm_title("Penny Lane"): 163}
    # discogs row: raw title annotated, clean_title canonical, duration NULL
    discogs_tracks = [("A2", "Penny Lane (2017 Mix)", None, "Penny Lane")]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE tracks (release_id INT, position TEXT, title TEXT, duration_seconds INT, clean_title TEXT)")
    con.execute("INSERT INTO tracks VALUES (1,'A2','Penny Lane (2017 Mix)',NULL,'Penny Lane')")
    con.commit()
    n = _enrich._fill_null_durations_by_title(con, 1, discogs_tracks, mb_by_title)
    assert n == 1
    assert con.execute("SELECT duration_seconds FROM tracks WHERE position='A2'").fetchone()[0] == 163


def test_recording_level_fallback_uses_clean_title():
    """_apply_recording_level_fallback must match on clean_title when the
    raw title has annotations like '(2017 Mix)' that don't appear in the
    MusicBrainz recording map keyed by the canonical clean title."""
    import asyncio
    import sqlite3
    from scripts.discogs import _enrich

    # Build an in-memory DB with the tracks table that _apply_recording_level_fallback
    # queries directly (SELECT position, title, clean_title FROM tracks WHERE ...).
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute(
        "CREATE TABLE tracks ("
        "  release_id INTEGER NOT NULL, "
        "  position TEXT, "
        "  title TEXT, "
        "  duration_seconds INTEGER, "
        "  clean_title TEXT, "
        "  PRIMARY KEY (release_id, position)"
        ")"
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, title, duration_seconds, clean_title) "
        "VALUES (1, 'A2', 'Penny Lane (2017 Mix)', NULL, 'Penny Lane')"
    )

    # Recording-MBID map keyed by the normalized CLEAN title — as MusicBrainz
    # catalogs it — not the annotated raw title.
    mb_by_title_rec = {_enrich._norm_title("Penny Lane"): "rec-penny-lane-mbid"}

    async def _fake_recording_duration(mbid, **kw):
        return {"rec-penny-lane-mbid": 163}.get(mbid)

    async def _noop(*a, **kw):
        return None

    from unittest.mock import patch

    with patch(
        "nowplaying.coverart.fetch_recording_duration",
        side_effect=_fake_recording_duration,
    ), patch(
        "scripts.discogs._enrich.asyncio.sleep",
        side_effect=_noop,
    ):
        updated = asyncio.run(
            _enrich._apply_recording_level_fallback(
                con,
                release_id=1,
                mb_by_title_rec=mb_by_title_rec,
                recording_cache=None,
            )
        )

    assert updated == 1, (
        "recording-level fallback must match on clean_title, not raw annotated title"
    )
    dur = con.execute(
        "SELECT duration_seconds FROM tracks WHERE release_id=1 AND position='A2'"
    ).fetchone()[0]
    assert dur == 163
