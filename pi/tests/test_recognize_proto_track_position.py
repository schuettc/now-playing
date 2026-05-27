"""Tests for discovered-path track_position + side resolution.

Shazam returns a track title; we walk the discovered tracklist for a
matching row and surface its position + side. Headliner regression
captures the live-verified 2026-05-27 Brothers / "Sinister Kid" → A10
case that originally motivated the fix.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest import mock

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_SCRIPTS = _PI_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


import recognize_proto  # noqa: E402


def _brothers_tracks() -> list[dict]:
    """Live-captured Brothers (Black Keys) MB tracklist, 18 rows, all
    marked side 'A' because MB treats the deluxe edition as a single
    medium. Position numbering is sequential A1–A18."""
    titles = [
        "Everlasting Light", "Next Girl", "Tighten Up", "Howlin' for You",
        "She's Long Gone", "Black Mud", "The Only One", "Too Afraid to Love You",
        "Ten Cent Pistol", "Sinister Kid", "The Go Getter", "I'm Not the One",
        "Unknown Brother", "Never Gonna Give You Up", "These Days",
        "Black Mud (Part II)", "Chop and Change", "Keep My Name Outta Your Mouth",
    ]
    return [
        {"position": f"A{i + 1}", "side": "A", "title": title,
         "duration_seconds": 200}
        for i, title in enumerate(titles)
    ]


def _shazam(title: str, album: str = "Brothers",
            artist: str = "The Black Keys") -> dict:
    return {
        "title": title,
        "subtitle": artist,
        "artist": artist,
        "album": album,
        "raw": {},
    }


def _run_with(sh: dict, tracks: list[dict],
              mbid: str = "brothers-mb-1") -> dict:
    catalog_release = {
        "mbid": mbid,
        "artist": sh["subtitle"],
        "title": sh["album"],
        "tracks": tracks,
    }
    with mock.patch.object(
        recognize_proto.shazam, "identify",
        new=mock.AsyncMock(return_value=sh),
    ), mock.patch.object(
        recognize_proto.discogs_catalog, "find_by_artist_title",
        return_value=None,
    ), mock.patch.object(
        recognize_proto.musicbrainz_lookup,
        "find_discovered_release_by_artist_album",
        return_value=mbid,
    ), mock.patch(
        "nowplaying.catalog.get_release", return_value=catalog_release,
    ):
        return asyncio.run(recognize_proto.recognize(Path("/tmp/fake.wav")))


def test_brothers_sinister_kid_resolves_to_a10():
    """Headliner live regression. Captured 2026-05-27 on the Pi: Shazam
    returns 'Sinister Kid', tracklist row 10 should win."""
    out = _run_with(_shazam("Sinister Kid"), _brothers_tracks())
    assert out["track_position"] == "A10"
    assert out["side"] == "A"


def test_curly_quote_normalizes_through():
    """Shazam returns 'She's Long Gone' with a curly apostrophe; the
    tracklist has the straight apostrophe. `_normalize` strips both
    down to 'shes long gone'."""
    tracks = _brothers_tracks()
    # Shazam side uses curly quote (U+2019).
    out = _run_with(_shazam("She’s Long Gone"), tracks)
    assert out["track_position"] == "A5"
    assert out["side"] == "A"


def test_substring_fallthrough_matches_parenthetical_suffix():
    """Shazam reports 'Tighten Up (Live)'; tracklist row 3 is 'Tighten
    Up'. Exact-normalize misses (parenthetical stripped only on the
    tracklist side has no effect — both sides normalize, but the Live
    suffix outside parens stays). Substring path picks it up."""
    out = _run_with(_shazam("Tighten Up Live"), _brothers_tracks())
    assert out["track_position"] == "A3"
    assert out["side"] == "A"


def test_no_match_leaves_fields_none(caplog):
    """Title not in tracklist → both fields stay unset, debug log fires,
    no exception."""
    caplog.set_level(logging.DEBUG, logger="recognize_proto")
    out = _run_with(_shazam("Some Title We Never Persisted"),
                    _brothers_tracks())
    assert out.get("track_position") is None
    assert out.get("side") is None
    assert any(
        "not matched against tracklist" in rec.message
        for rec in caplog.records
    )


def test_ambiguous_exact_match_leaves_fields_none():
    """Two rows normalize to the same title → don't guess; leave null."""
    tracks = [
        {"position": "A1", "side": "A", "title": "Intro",
         "duration_seconds": 100},
        {"position": "B1", "side": "B", "title": "Intro",
         "duration_seconds": 100},
    ]
    out = _run_with(_shazam("Intro", album="Some Album"), tracks)
    assert out.get("track_position") is None
    assert out.get("side") is None


def test_tracklist_still_attached_when_no_title_match():
    """Even when title resolution fails, the tracklist itself still
    populates so kiosk has something to render."""
    out = _run_with(_shazam("Unknown Track"), _brothers_tracks())
    assert out.get("tracklist")
    assert len(out["tracklist"]) == 18
    assert out["release_mbid"] == "brothers-mb-1"
