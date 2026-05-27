"""Tests for the recognize_proto integration of disambiguated_album.

`_release_fields` is the single funnel that converts a Discogs release
dict into a publish-payload dict. The publish `album` field must use
`disambiguated_album` when present (eponymous LPs) and fall back to the
bare canonical `title` otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

_SCRIPTS = _PI_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import recognize_proto  # noqa: E402


def test_release_fields_uses_disambiguated_album_when_present():
    """Eponymous LP: publish payload's `album` carries the year suffix."""
    rel = {
        "id": 2,
        "artist": "American Football",
        "title": "American Football",
        "disambiguated_album": "American Football (2016)",
        "year": 2016,
        "label": "Polyvinyl",
        "catno": "POLY-039",
        "art_path": "/var/lib/nowplaying/art/2.jpg",
        "tracks": [],
    }
    out = recognize_proto._release_fields(rel)
    assert out["album"] == "American Football (2016)"
    # Canonical title stays bare so /identify search still works.
    assert out["title"] == "American Football"


def test_release_fields_falls_back_to_title_when_unambiguous():
    """Non-eponymous LP: publish payload's `album` is the bare title."""
    rel = {
        "id": 100,
        "artist": "Neil Young",
        "title": "Harvest",
        "year": 1972,
        "label": "Reprise",
        "catno": "MS-2032",
        "art_path": "/var/lib/nowplaying/art/100.jpg",
        "tracks": [],
    }
    out = recognize_proto._release_fields(rel)
    assert out["album"] == "Harvest"
    assert out["title"] == "Harvest"


def test_release_fields_ignores_empty_disambiguated_album():
    """Empty-string disambiguated_album doesn't override title."""
    rel = {
        "id": 7,
        "artist": "Test",
        "title": "Real Title",
        "disambiguated_album": "",
        "tracks": [],
    }
    out = recognize_proto._release_fields(rel)
    assert out["album"] == "Real Title"
