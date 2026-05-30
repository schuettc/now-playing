"""Tests for eponymous-album disambiguation in `discogs.catalog`.

Live bug 2026-05-27: American Football LP1 (1999), LP2 (2016), and LP3
(2019) all share the canonical title "American Football". The kiosk,
history, and Last.fm scrobble all displayed "American Football" for
tracks from three different LPs. `get_release` now emits a
`disambiguated_album` field for ambiguous releases.
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.discogs import catalog as discogs_catalog  # noqa: E402


@contextmanager
def _catalog_db(releases):
    """Build an in-memory Discogs catalog with the given release rows.

    `releases` is a list of dicts: id, artist, title, year, catno.
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE releases ("
        "  id INTEGER PRIMARY KEY,"
        "  artist TEXT,"
        "  title TEXT,"
        "  year INTEGER,"
        "  country TEXT,"
        "  format TEXT,"
        "  label TEXT,"
        "  catno TEXT,"
        "  primary_image_url TEXT,"
        "  art_path TEXT"
        ")"
    )
    con.execute(
        "CREATE TABLE tracks ("
        "  release_id INTEGER NOT NULL,"
        "  position TEXT,"
        "  side TEXT,"
        "  title TEXT,"
        "  duration_seconds INTEGER,"
        "  clean_title TEXT,"
        "  clean_title_source TEXT,"
        "  is_suite_parent INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    for r in releases:
        con.execute(
            "INSERT INTO releases (id, artist, title, year, catno) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["id"], r["artist"], r["title"], r.get("year"), r.get("catno")),
        )
    con.commit()
    # Clear lru_cache between fixtures so previous tests don't poison.
    discogs_catalog._ambiguous_titles_for_artist.cache_clear()
    discogs_catalog.rid_to_album.cache_clear()
    discogs_catalog.first_position_per_side.cache_clear()
    try:
        yield con
    finally:
        con.close()
        discogs_catalog._ambiguous_titles_for_artist.cache_clear()


@pytest.fixture
def american_football_db():
    """The exact live-bug fixture: three "American Football" releases."""
    rows = [
        {"id": 1, "artist": "American Football",
         "title": "American Football", "year": 1999, "catno": "POLY-006"},
        {"id": 2, "artist": "American Football",
         "title": "American Football", "year": 2016, "catno": "POLY-039"},
        {"id": 3, "artist": "American Football",
         "title": "American Football", "year": 2019, "catno": "POLY-080"},
    ]
    with _catalog_db(rows) as con:
        yield con


def test_eponymous_three_lps_get_year_suffix(american_football_db):
    """Each of the three American Football LPs returns a year-suffixed
    `disambiguated_album` matching its release year."""
    with patch.object(discogs_catalog, "open_ro",
                      return_value=american_football_db):
        r1 = discogs_catalog.get_release(1)
        r2 = discogs_catalog.get_release(2)
        r3 = discogs_catalog.get_release(3)
    assert r1["disambiguated_album"] == "American Football (1999)"
    assert r2["disambiguated_album"] == "American Football (2016)"
    assert r3["disambiguated_album"] == "American Football (2019)"
    # Title stays canonical for /identify search.
    assert r1["title"] == "American Football"
    assert r2["title"] == "American Football"


def test_unique_release_has_no_disambiguation():
    """A release with a unique title has no `disambiguated_album` key."""
    rows = [
        {"id": 10, "artist": "Neil Young",
         "title": "Harvest", "year": 1972, "catno": "MS-2032"},
    ]
    with _catalog_db(rows) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            rel = discogs_catalog.get_release(10)
    assert "disambiguated_album" not in rel
    assert rel["title"] == "Harvest"


def test_same_title_same_year_falls_back_to_catno():
    """When ≥2 releases share both title AND year, year alone can't
    disambiguate — fall back to a catno suffix."""
    rows = [
        {"id": 20, "artist": "Reissue Band",
         "title": "Boxset", "year": 2020, "catno": "BOX-A"},
        {"id": 21, "artist": "Reissue Band",
         "title": "Boxset", "year": 2020, "catno": "BOX-B"},
    ]
    with _catalog_db(rows) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            r20 = discogs_catalog.get_release(20)
            r21 = discogs_catalog.get_release(21)
    assert r20["disambiguated_album"] == "Boxset (BOX-A)"
    assert r21["disambiguated_album"] == "Boxset (BOX-B)"


def test_null_year_no_artifact():
    """When `year` is null and there's no catno tiebreaker, leave the
    bare title — never render "(None)"."""
    rows = [
        {"id": 30, "artist": "Unknown",
         "title": "Untitled", "year": None, "catno": None},
        {"id": 31, "artist": "Unknown",
         "title": "Untitled", "year": None, "catno": None},
    ]
    with _catalog_db(rows) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            rel = discogs_catalog.get_release(30)
    assert "disambiguated_album" not in rel
    assert rel["title"] == "Untitled"


def test_null_year_with_catno_uses_catno():
    """Null year + present catno → catno-suffix fallback."""
    rows = [
        {"id": 40, "artist": "Demo Tapes",
         "title": "Sessions", "year": None, "catno": "DEMO-1"},
        {"id": 41, "artist": "Demo Tapes",
         "title": "Sessions", "year": None, "catno": "DEMO-2"},
    ]
    with _catalog_db(rows) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            r40 = discogs_catalog.get_release(40)
    assert r40["disambiguated_album"] == "Sessions (DEMO-1)"


def test_case_insensitive_match():
    """The ambiguity check is case-insensitive."""
    rows = [
        {"id": 50, "artist": "Crystal Castles",
         "title": "Crystal Castles", "year": 2008, "catno": "FIC-1"},
        {"id": 51, "artist": "crystal castles",
         "title": "CRYSTAL CASTLES", "year": 2010, "catno": "FIC-2"},
    ]
    with _catalog_db(rows) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            r50 = discogs_catalog.get_release(50)
            r51 = discogs_catalog.get_release(51)
    assert r50["disambiguated_album"] == "Crystal Castles (2008)"
    assert r51["disambiguated_album"] == "CRYSTAL CASTLES (2010)"
