"""Regression: every catalog entrypoint must return a sentinel (not raise)
when ``discogs.sqlite`` is missing. Live bug 2026-05-27 — Shazam hit
crashed the orchestrator because ``_find_by_artist_title_primary`` and
``get_release`` re-raised ``sqlite3.OperationalError`` from ``open_ro()``.
"""
from __future__ import annotations

import pytest

from nowplaying.discogs import catalog


@pytest.fixture
def missing_db(monkeypatch, tmp_path):
    """Point catalog.DB_PATH at a nonexistent file and clear LRU caches."""
    monkeypatch.setattr(catalog, "DB_PATH", tmp_path / "nope.sqlite")
    catalog.rid_to_album.cache_clear()
    catalog.first_position_per_side.cache_clear()
    return tmp_path / "nope.sqlite"


def test_find_by_artist_title_returns_none_when_db_missing(missing_db):
    assert catalog.find_by_artist_title("Foo Fighters", "Everlong") is None


def test_find_by_artist_title_with_preferred_release_returns_none(missing_db):
    # Exercises the suite_fallback + suite_artist_scan branches too.
    assert (
        catalog.find_by_artist_title(
            "Foo Fighters", "Everlong", preferred_release_id=42,
        )
        is None
    )


def test_get_release_returns_none_when_db_missing(missing_db):
    assert catalog.get_release(42) is None


def test_suite_artist_scan_returns_none_when_db_missing(missing_db):
    assert catalog._suite_artist_scan("Foo Fighters") is None


def test_rid_to_album_returns_none_when_db_missing(missing_db):
    # Already protected; confirm still well-behaved.
    assert catalog.rid_to_album(42) is None


def test_first_position_per_side_returns_empty_when_db_missing(missing_db):
    # Already protected; confirm still well-behaved.
    assert catalog.first_position_per_side(42) == {}
