"""AirPlay reports the album it is playing; the Discogs lookup must honour it.

Observed live: Apple Music playing the White Album resolved "Back in the
U.S.S.R." to the 1967-1970 compilation and "While My Guitar Gently Weeps"
to the Anthology Collection box, because the lookup matched on artist and
song title alone.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.discogs import catalog

RELEASES = [
    (4033122, "The Beatles", "The Beatles", 2012),
    (28859359, "The Beatles", "1967-1970", 2023),
    (13984943, "The Beatles", "Sgt. Pepper's Lonely Hearts Club Band", 2017),
]
TRACKS = [
    (4033122, "A1", "A", "Back In The U.S.S.R."),
    (28859359, "A1", "A", "Back In The U.S.S.R."),
    (13984943, "A1", "A", "Sgt. Pepper's Lonely Hearts Club Band"),
]


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    from scripts.discogs import _db

    path = tmp_path / "discogs.sqlite"
    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    con.executemany("INSERT INTO releases (id, artist, title, year) VALUES (?, ?, ?, ?)", RELEASES)
    con.executemany(
        "INSERT INTO tracks (release_id, position, side, title, duration_seconds, is_suite_parent) "
        "VALUES (?, ?, ?, ?, 200, 0)",
        TRACKS,
    )
    con.close()
    catalog.rid_to_album.cache_clear()
    catalog.first_position_per_side.cache_clear()
    monkeypatch.setattr(catalog, "DB_PATH", path)
    yield
    catalog.rid_to_album.cache_clear()
    catalog.first_position_per_side.cache_clear()


def test_title_only_lookup_prefers_the_newer_compilation() -> None:
    rel = catalog.find_by_artist_title("The Beatles", "Back in the U.S.S.R.")
    assert rel is not None and rel["id"] == 28859359


@pytest.mark.parametrize(
    ("album", "expected"),
    [
        ("The Beatles", 4033122),
        ("The Beatles (Remastered)", 4033122),
        ("1967-1970", 28859359),
        ("Sgt. Pepper's Lonely Hearts Club Band", None),
    ],
)
def test_album_scoped_lookup(album: str, expected: int | None) -> None:
    rel = catalog.find_by_artist_title("The Beatles", "Back in the U.S.S.R.", album=album)
    assert (rel["id"] if rel else None) == expected
