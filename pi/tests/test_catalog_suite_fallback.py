"""Tests for the multi-part-suite fallback in `catalog.find_by_artist_title`.

Background: Discogs stores multi-movement suite tracks as separate rows
with positions like ``D1. I``, ``D1. II``, ``D1. III`` and titles set to
the *leaf* movement names ("The Death Of St. Jimmy", "East 12th St.").
Shazam, however, returns the *parent* suite name ("Homecoming"). Without
the fallback, reverse-lookup of "Homecoming" finds no track-title match,
returns None, and the kiosk loses the sticky release_id (album art + tracklist).

The fallback fires only when:
  1. Primary scoring + slash-split both produced no hit.
  2. ``preferred_release_id`` is set (we have a sticky album in flight).
  3. The sticky release contains multi-part positions (``X1. I`` shape).
  4. The sticky release's artist matches Shazam's artist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.discogs import catalog  # noqa: E402


class _FakeRow(dict):
    """SQLite row stand-in supporting both ``row["col"]`` and ``row.col``."""

    def __getattr__(self, k):
        return self[k]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *_a, **_kw):
        return _FakeCursor(self._rows)


def _mock_open_ro(rows):
    return lambda: _FakeConn(rows)


AMERICAN_IDIOT = {
    "id": 32110209,
    "artist": "Green Day",
    "title": "American Idiot",
    "tracks": [
        {"position": "A1", "side": "A", "title": "American Idiot"},
        {"position": "A2. I", "side": "A", "title": "Jesus Of Suburbia"},
        {"position": "A2. II", "side": "A", "title": "City Of The Damned"},
        {"position": "D1. I", "side": "D", "title": "The Death Of St. Jimmy"},
        {"position": "D1. II", "side": "D", "title": "East 12th St."},
        {"position": "D1. III", "side": "D", "title": "Nobody Likes You"},
    ],
}

PLAIN_LP = {
    "id": 555,
    "artist": "Some Band",
    "title": "Plain Album",
    "tracks": [
        {"position": "A1", "side": "A", "title": "Track One"},
        {"position": "A2", "side": "A", "title": "Track Two"},
        {"position": "B1", "side": "B", "title": "Track Three"},
    ],
}


def _primary_returns_none(artist, title, *, preferred_release_id=None):
    return None


def test_suite_fallback_returns_preferred_release():
    """Shazam returns the suite parent name; fallback returns the sticky release."""
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=dict(AMERICAN_IDIOT)):
        result = catalog.find_by_artist_title(
            "Green Day", "Homecoming",
            preferred_release_id=32110209,
        )
    assert result is not None
    assert result["id"] == 32110209
    # No matched track: caller's positional guess should be preserved.
    assert result.get("matched_track_position") is None
    assert result.get("matched_track_title") is None
    assert result.get("suite_fallback") is True


def test_artist_scan_fires_without_preferred_release():
    """No sticky → artist-scan path finds the unique multi-part release
    by this artist in the collection. Cold-start of side D."""
    rows = [_FakeRow(release_id=32110209, artist="Green Day")]
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=dict(AMERICAN_IDIOT)), \
         patch.object(catalog, "open_ro", _mock_open_ro(rows)):
        result = catalog.find_by_artist_title("Green Day", "Homecoming")
    assert result is not None
    assert result["id"] == 32110209
    assert result.get("suite_fallback") is True


def test_artist_scan_declines_on_ambiguous_artist():
    """Two multi-part releases by the same artist → ambiguous, decline
    rather than guess wrong."""
    rows = [
        _FakeRow(release_id=32110209, artist="Green Day"),
        _FakeRow(release_id=99999, artist="Green Day"),
    ]
    other_release = dict(AMERICAN_IDIOT, id=99999, title="Other Suite")
    releases = {32110209: dict(AMERICAN_IDIOT), 99999: other_release}
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", side_effect=lambda rid: releases[rid]), \
         patch.object(catalog, "open_ro", _mock_open_ro(rows)):
        result = catalog.find_by_artist_title("Green Day", "Homecoming")
    assert result is None


def test_artist_scan_skips_plain_lp_candidates():
    """Artist has releases in the collection but none have multi-part
    positions → no fallback."""
    rows = [_FakeRow(release_id=555, artist="Some Band")]
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=dict(PLAIN_LP)), \
         patch.object(catalog, "open_ro", _mock_open_ro(rows)):
        result = catalog.find_by_artist_title("Some Band", "Unknown Song")
    assert result is None


def test_artist_scan_declines_on_artist_mismatch():
    """Row artist doesn't fuzzy-match Shazam's artist → not a candidate."""
    rows = [_FakeRow(release_id=32110209, artist="Green Day")]
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=dict(AMERICAN_IDIOT)), \
         patch.object(catalog, "open_ro", _mock_open_ro(rows)):
        result = catalog.find_by_artist_title("Pink Floyd", "Homecoming")
    assert result is None


def test_fallback_skipped_for_plain_lp():
    """Preferred release has no multi-part positions → sticky path declines.
    Artist-scan also declines because the same plain LP is the only
    candidate row in the collection."""
    rows = [_FakeRow(release_id=555, artist="Some Band")]
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=dict(PLAIN_LP)), \
         patch.object(catalog, "open_ro", _mock_open_ro(rows)):
        result = catalog.find_by_artist_title(
            "Some Band", "Unknown Song",
            preferred_release_id=555,
        )
    assert result is None


def test_fallback_skipped_when_artist_differs():
    """Sticky release artist doesn't match Shazam's → sticky path declines.
    Artist-scan also finds no matching candidate (no rows for "Pink Floyd")."""
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=dict(AMERICAN_IDIOT)), \
         patch.object(catalog, "open_ro", _mock_open_ro([])):
        result = catalog.find_by_artist_title(
            "Pink Floyd", "Homecoming",
            preferred_release_id=32110209,
        )
    assert result is None


def test_primary_hit_short_circuits_fallback():
    """When the primary lookup returns a hit, fallback never runs."""
    primary_hit = {"id": 999, "match_score": 80, "title": "Some Album"}
    get_release_calls: list[int] = []

    def _track_get_release(rid):
        get_release_calls.append(rid)
        return dict(AMERICAN_IDIOT)

    with patch.object(catalog, "_find_by_artist_title_primary", return_value=primary_hit), \
         patch.object(catalog, "get_release", side_effect=_track_get_release):
        result = catalog.find_by_artist_title(
            "Green Day", "Holiday",
            preferred_release_id=32110209,
        )
    assert result is not None
    assert result["id"] == 999
    # Fallback never triggered → get_release not called from the fallback path.
    assert get_release_calls == []


def test_fallback_skipped_when_release_missing():
    """get_release returns None (catalog drift) for the sticky AND no
    artist-scan candidates → both fallbacks return None, doesn't blow up."""
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_primary_returns_none), \
         patch.object(catalog, "get_release", return_value=None), \
         patch.object(catalog, "open_ro", _mock_open_ro([])):
        result = catalog.find_by_artist_title(
            "Green Day", "Homecoming",
            preferred_release_id=32110209,
        )
    assert result is None
