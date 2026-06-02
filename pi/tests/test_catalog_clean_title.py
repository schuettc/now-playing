"""Tests that get_release() surfaces tracks.clean_title in both catalog backends.

Task 6: Verify clean_title appears in the tracks list returned by both:
  - nowplaying.discogs.catalog.get_release(release_id) — Discogs path
  - nowplaying.catalog.get_release(mbid=...) — discovered path
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_discogs_db(path: Path) -> None:
    """Create a minimal Discogs DB at path with one release + one track."""
    from scripts.discogs import _db

    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    con.execute(
        "INSERT INTO releases (id, artist, title, year) VALUES (1, 'Artist', 'Album', 2017)",
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, side, title, duration_seconds, "
        "is_suite_parent, clean_title) VALUES (1, 'A1', 'A', 'Song (2017 Mix)', 240, 0, 'Song')",
    )
    con.close()


def _make_discovered_db(path: Path) -> None:
    """Create a minimal discovered.sqlite at path with one release + one track."""
    from nowplaying.discovery.schema import init_db

    init_db(path)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO releases (mbid, artist, title, year) VALUES "
        "('test-mbid-1', 'Artist', 'Album', 2017)",
    )
    con.execute(
        "INSERT INTO tracks (mbid, position, side, title, duration_seconds, clean_title) "
        "VALUES ('test-mbid-1', 'A1', 'A', 'Song (2017 Mix)', 240, 'Song')",
    )
    con.commit()
    con.close()


# ── discogs path ──────────────────────────────────────────────────────────────


def test_discogs_get_release_surfaces_clean_title(monkeypatch, tmp_path):
    """Discogs catalog.get_release should include clean_title in each track dict."""
    db_path = tmp_path / "discogs.sqlite"
    _make_discogs_db(db_path)

    from nowplaying.discogs import catalog

    # Clear lru_caches so the patched DB_PATH is picked up on first access.
    catalog.rid_to_album.cache_clear()
    catalog.first_position_per_side.cache_clear()

    monkeypatch.setattr(catalog, "DB_PATH", db_path)

    result = catalog.get_release(1)
    assert result is not None
    tracks = result["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["clean_title"] == "Song"


# ── discovered path ───────────────────────────────────────────────────────────


def test_discovered_get_release_surfaces_clean_title(monkeypatch, tmp_path):
    """Discovered catalog._get_discovered_release should include clean_title in tracks."""
    db_path = tmp_path / "discovered.sqlite"
    _make_discovered_db(db_path)

    from nowplaying.discovery import schema
    from nowplaying.discovery import musicbrainz_lookup as mbl

    orig_open_ro = schema.open_ro

    def _ro(p=db_path):
        return orig_open_ro(p)

    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(mbl, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(schema, "open_ro", _ro)
    monkeypatch.setattr(mbl, "open_ro", _ro)
    # catalog imports open_ro at module import time; patch its binding directly.
    monkeypatch.setattr("nowplaying.catalog._discovered_open_ro", _ro)

    from nowplaying import catalog as catalog_dispatch

    result = catalog_dispatch.get_release(mbid="test-mbid-1")
    assert result is not None
    tracks = result["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["clean_title"] == "Song"


# ── reverse-lookup surfaces matched_track_clean_title ─────────────────────────


def _make_discogs_db_beatles(path: Path) -> None:
    """Create a minimal Discogs DB with a Beatles release + a track with clean_title."""
    from scripts.discogs import _db

    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.DDL)
    _db._migrate_schema(con)
    con.execute(
        "INSERT INTO releases (id, artist, title, year) VALUES (1, 'The Beatles', 'Magical Mystery Tour', 1967)",
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, side, title, duration_seconds, "
        "is_suite_parent, clean_title) VALUES (1, 'A2', 'A', 'Penny Lane (2017 Mix)', 180, 0, 'Penny Lane')",
    )
    con.close()


def test_reverse_lookup_surfaces_matched_track_clean_title(monkeypatch, tmp_path):
    """find_by_artist_title should surface matched_track_clean_title on the result."""
    db_path = tmp_path / "discogs.sqlite"
    _make_discogs_db_beatles(db_path)

    from nowplaying.discogs import catalog

    catalog.rid_to_album.cache_clear()
    catalog.first_position_per_side.cache_clear()

    monkeypatch.setattr(catalog, "DB_PATH", db_path)

    rel = catalog.find_by_artist_title(artist="The Beatles", title="Penny Lane")
    assert rel is not None
    assert rel["matched_track_clean_title"] == "Penny Lane"
