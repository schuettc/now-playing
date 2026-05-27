"""Tests for eponymous-album disambiguation in the discovered (MB) path.

When ≥2 discovered.sqlite releases by the same artist share a canonical
title, `nowplaying.catalog._get_discovered_release` adds a
`disambiguated_album` field — same shape as the Discogs path so
`recognize_proto._attach_discovered_or_schedule` can plumb it onto the
publish payload.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def af_discovered_db(monkeypatch, tmp_path):
    """Three American Football releases with distinct years + MBIDs."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    from nowplaying.discovery import schema
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
    monkeypatch.setattr("nowplaying.catalog._discovered_open_ro", _ro)
    schema.init_db(db_path)
    for mbid, year in [
        ("af1999-mb-id-aaaa", 1999),
        ("af2016-mb-id-bbbb", 2016),
        ("af2019-mb-id-cccc", 2019),
    ]:
        _run(mbl.persist({
            "mbid": mbid,
            "artist": "American Football",
            "album": "American Football",
            "year": year,
            "tracks": [
                {"position": "A1", "side": "A",
                 "title": "Track A1", "duration_seconds": 180},
            ],
        }))
    return db_path


def test_discovered_eponymous_year_suffix(af_discovered_db):
    from nowplaying import catalog as catalog_dispatch
    out1 = catalog_dispatch.get_release(mbid="af1999-mb-id-aaaa")
    out2 = catalog_dispatch.get_release(mbid="af2016-mb-id-bbbb")
    out3 = catalog_dispatch.get_release(mbid="af2019-mb-id-cccc")
    assert out1["disambiguated_album"] == "American Football (1999)"
    assert out2["disambiguated_album"] == "American Football (2016)"
    assert out3["disambiguated_album"] == "American Football (2019)"
    # Canonical title unchanged.
    assert out1["title"] == "American Football"


def test_discovered_unambiguous_release_no_suffix(af_discovered_db):
    """A release whose title is unique in discovered.sqlite gets no
    disambiguation."""
    from nowplaying import catalog as catalog_dispatch
    from nowplaying.discovery import musicbrainz_lookup as mbl
    _run(mbl.persist({
        "mbid": "harvest-mb-id",
        "artist": "Neil Young",
        "album": "Harvest",
        "year": 1972,
        "tracks": [
            {"position": "A1", "side": "A",
             "title": "Out On The Weekend", "duration_seconds": 271},
        ],
    }))
    out = catalog_dispatch.get_release(mbid="harvest-mb-id")
    assert "disambiguated_album" not in out


def test_discovered_same_year_falls_back_to_mbid_prefix(
    monkeypatch, tmp_path,
):
    """When two discovered releases share both title AND year (no catno
    column in the discovered schema), fall back to a short-mbid suffix."""
    from nowplaying.discovery import musicbrainz_lookup as mbl
    from nowplaying.discovery import schema
    db_path = tmp_path / "discovered.sqlite"
    monkeypatch.setattr(schema, "DISCOVERED_DB_PATH", db_path)
    monkeypatch.setattr(mbl, "DISCOVERED_DB_PATH", db_path)
    orig_open_ro = schema.open_ro
    orig_open_rw = schema.open_rw
    monkeypatch.setattr(schema, "open_ro",
                        lambda p=db_path: orig_open_ro(p))
    monkeypatch.setattr(schema, "open_rw",
                        lambda p=db_path: orig_open_rw(p))
    monkeypatch.setattr(mbl, "open_ro",
                        lambda p=db_path: orig_open_ro(p))
    monkeypatch.setattr(mbl, "open_rw",
                        lambda p=db_path: orig_open_rw(p))
    monkeypatch.setattr("nowplaying.catalog._discovered_open_ro",
                        lambda p=db_path: orig_open_ro(p))
    schema.init_db(db_path)
    _run(mbl.persist({
        "mbid": "deadbeef-aaaa", "artist": "Same Year",
        "album": "Reissue", "year": 2020,
        "tracks": [{"position": "A1", "side": "A",
                    "title": "x", "duration_seconds": 100}],
    }))
    _run(mbl.persist({
        "mbid": "cafef00d-bbbb", "artist": "Same Year",
        "album": "Reissue", "year": 2020,
        "tracks": [{"position": "A1", "side": "A",
                    "title": "x", "duration_seconds": 100}],
    }))
    from nowplaying import catalog as catalog_dispatch
    out_a = catalog_dispatch.get_release(mbid="deadbeef-aaaa")
    out_b = catalog_dispatch.get_release(mbid="cafef00d-bbbb")
    assert out_a["disambiguated_album"] == "Reissue (deadbeef)"
    assert out_b["disambiguated_album"] == "Reissue (cafef00d)"
