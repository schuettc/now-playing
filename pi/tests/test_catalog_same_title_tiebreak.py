"""Two tracks of one release whose titles differ only in a parenthetical."""
from __future__ import annotations

import sqlite3

import pytest

from nowplaying.discogs import catalog

TRACKS = [
    (1, "A1", "A", "Shine On You Crazy Diamond (Parts 1-5)"),
    (1, "A2", "A", "Welcome To The Machine"),
    (1, "B1", "B", "Have A Cigar"),
    (1, "B2", "B", "Wish You Were Here"),
    (1, "B3", "B", "Shine On You Crazy Diamond (Parts 6-9)"),
]


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    from scripts.discogs import _db

    path = tmp_path / "discogs.sqlite"
    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    con.execute(
        "INSERT INTO releases (id, artist, title, year) "
        "VALUES (1, 'Pink Floyd', 'Wish You Were Here', 1975)"
    )
    con.executemany(
        "INSERT INTO tracks (release_id, position, side, title, duration_seconds, is_suite_parent) "
        "VALUES (?, ?, ?, ?, 200, 0)",
        TRACKS,
    )
    con.close()
    catalog.first_position_per_side.cache_clear()
    monkeypatch.setattr(catalog, "DB_PATH", path)
    yield
    catalog.first_position_per_side.cache_clear()


def test_parenthetical_picks_the_track_it_names() -> None:
    rel = catalog.find_by_artist_title(
        "Pink Floyd", "Shine On You Crazy Diamond (Parts 6-9)",
    )
    assert rel is not None
    assert rel["matched_track_position"] == "B3"
    assert rel["matched_track_title"] == "Shine On You Crazy Diamond (Parts 6-9)"


def test_bare_title_still_prefers_the_side_opener() -> None:
    rel = catalog.find_by_artist_title("Pink Floyd", "Shine On You Crazy Diamond")
    assert rel is not None and rel["matched_track_position"] == "A1"
