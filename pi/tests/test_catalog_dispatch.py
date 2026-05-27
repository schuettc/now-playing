"""Tests for nowplaying.catalog — the Discogs/discovered dispatcher.

catalog.get_release(release_id=42) → routes to discogs.catalog.
catalog.get_release(mbid="abc") → routes to discovered.sqlite.
Both return release dicts with a comparable shape.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def discovered_db(monkeypatch, tmp_path):
    from nowplaying.discovery import schema
    from nowplaying.discovery import musicbrainz_lookup as mbl
    db_path = tmp_path / "discovered.sqlite"
    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(mbl, "DISCOVERED_DB_PATH", db_path)
    orig_open_ro = schema.open_ro
    orig_open_rw = schema.open_rw

    def _ro(p=db_path):
        return orig_open_ro(p)

    def _rw(p=db_path):
        return orig_open_rw(p)

    monkeypatch.setattr(schema, "open_ro", _ro)
    monkeypatch.setattr(schema, "open_rw", _rw)
    monkeypatch.setattr(mbl, "open_ro", _ro)
    monkeypatch.setattr(mbl, "open_rw", _rw)
    # The catalog module imports open_ro at import time; patch its binding too.
    monkeypatch.setattr(
        "nowplaying.catalog._discovered_open_ro", _ro,
    )
    schema.init_db(db_path)
    # Persist a fixture release.
    _run(mbl.persist({
        "mbid": "abc-mb",
        "artist": "Neil Young",
        "album": "Harvest",
        "year": 1972,
        "tracks": [
            {"position": "A1", "side": "A",
             "title": "Out On The Weekend", "duration_seconds": 271},
            {"position": "A2", "side": "A",
             "title": "Harvest", "duration_seconds": 191},
            {"position": "B1", "side": "B",
             "title": "Heart Of Gold", "duration_seconds": 207},
        ],
    }))
    return db_path


def test_get_release_dispatches_to_discogs_by_release_id():
    """release_id=int → discogs.catalog.get_release."""
    from nowplaying import catalog as catalog_dispatch
    fake_rel = {
        "id": 42, "artist": "X", "title": "Y", "tracks": [],
    }
    with patch(
        "nowplaying.catalog.discogs_catalog.get_release",
        return_value=fake_rel,
    ) as m:
        out = catalog_dispatch.get_release(release_id=42)
    m.assert_called_once_with(42)
    assert out is fake_rel


def test_get_release_dispatches_to_discovered_by_mbid(discovered_db):
    """mbid=str → discovered.sqlite."""
    from nowplaying import catalog as catalog_dispatch
    out = catalog_dispatch.get_release(mbid="abc-mb")
    assert out is not None
    assert out["mbid"] == "abc-mb"
    assert out["artist"] == "Neil Young"
    assert out["title"] == "Harvest"
    assert len(out["tracks"]) == 3
    assert out["tracks"][0]["position"] == "A1"


def test_get_release_returns_none_when_neither_id_given():
    from nowplaying import catalog as catalog_dispatch
    assert catalog_dispatch.get_release() is None


def test_get_release_release_id_wins_when_both(discovered_db):
    """When both IDs passed, Discogs path takes precedence (canonical)."""
    from nowplaying import catalog as catalog_dispatch
    fake_rel = {"id": 42, "tracks": []}
    with patch(
        "nowplaying.catalog.discogs_catalog.get_release",
        return_value=fake_rel,
    ) as m:
        out = catalog_dispatch.get_release(release_id=42, mbid="abc-mb")
    m.assert_called_once_with(42)
    assert out is fake_rel


def test_get_release_unknown_mbid_returns_none(discovered_db):
    from nowplaying import catalog as catalog_dispatch
    assert catalog_dispatch.get_release(mbid="nonexistent") is None


def test_first_position_per_side_discogs():
    from nowplaying import catalog as catalog_dispatch
    with patch(
        "nowplaying.catalog.discogs_catalog.first_position_per_side",
        return_value={"A": "A1", "B": "B1"},
    ):
        out = catalog_dispatch.first_position_per_side(release_id=42)
    assert out == {"A": "A1", "B": "B1"}


def test_first_position_per_side_discovered(discovered_db):
    from nowplaying import catalog as catalog_dispatch
    out = catalog_dispatch.first_position_per_side(mbid="abc-mb")
    assert out == {"A": "A1", "B": "B1"}


def test_first_position_per_side_empty_when_neither():
    from nowplaying import catalog as catalog_dispatch
    assert catalog_dispatch.first_position_per_side() == {}


def test_rid_to_album_discogs():
    from nowplaying import catalog as catalog_dispatch
    with patch(
        "nowplaying.catalog.discogs_catalog.rid_to_album",
        return_value=("Neil Young", "Harvest"),
    ):
        out = catalog_dispatch.rid_to_album(release_id=42)
    assert out == ("Neil Young", "Harvest")


def test_rid_to_album_discovered(discovered_db):
    from nowplaying import catalog as catalog_dispatch
    out = catalog_dispatch.rid_to_album(mbid="abc-mb")
    assert out == ("Neil Young", "Harvest")


def test_rid_to_album_discovered_unknown(discovered_db):
    from nowplaying import catalog as catalog_dispatch
    assert catalog_dispatch.rid_to_album(mbid="nope") is None
