"""Tests for the tracklist-aware advancement helpers in nowplaying.main.

See docs/features/tracklist-aware-advancement/. The two pure functions
under test (`_advance_predicted_position` and `_build_predicted_payload`)
are the load-bearing logic for predicting the current track from a
locked album's tracklist when Shazam misses.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_SCRIPTS_ROOT = _PI_ROOT / "scripts"
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

# Importing nowplaying.main pulls in capture/runtime; that's fine for a
# unit test of pure module-level helpers — we never start the
# orchestrator loop.
from nowplaying import main as nowplaying_main  # noqa: E402


# Minimal Failure-Fantastic-Planet tracklist fragment, in DB insertion
# order (matches the side ordering of the real release).
FANTASTIC_PLANET_TRACKS = [
    {"position": "A1", "side": "A", "title": "Saturday Savior"},
    {"position": "A2", "side": "A", "title": "Sergeant Politeness"},
    {"position": "A3", "side": "A", "title": "Segue 1"},
    {"position": "A4", "side": "A", "title": "Smoking Umbrellas"},
    {"position": "B5", "side": "B", "title": "Pillowhead"},
    {"position": "B6", "side": "B", "title": "Blank"},
    {"position": "D15", "side": "D", "title": "Stuck On You"},
    {"position": "D16", "side": "D", "title": "Heliotropic"},
    {"position": "D17", "side": "D", "title": "Daylight"},
]


# ---- _advance_predicted_position -----------------------------------


def test_advance_normal():
    """D16 → D17 (advances by one on the same side)."""
    current = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D16",
        "index_in_side": 1,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    assert result is not None
    assert result["track_position"] == "D17"
    assert result["side"] == "D"
    assert result["index_in_side"] == 2
    assert result["release_id"] == 31427573


def test_advance_from_first_of_side():
    """D15 (the cumulative-numbered first of D-side) → D16."""
    current = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D15",
        "index_in_side": 0,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    assert result is not None
    assert result["track_position"] == "D16"


def test_advance_end_of_side_returns_none():
    """D17 is the last track of D side → no advance."""
    current = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D17",
        "index_in_side": 2,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    assert result is None


def test_advance_position_not_found_returns_none():
    """If track_position isn't on the side (data drift), bail safely."""
    current = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D99",
        "index_in_side": 0,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    assert result is None


def test_advance_cross_side_isolation():
    """A4 must not advance into B5 — different side."""
    current = {
        "release_id": 31427573,
        "side": "A",
        "track_position": "A4",
        "index_in_side": 3,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    # A side has A1-A4 only in our fixture; A4 is the last → None.
    assert result is None


def test_advance_missing_side_in_current():
    """Defensive: current dict without 'side' returns None."""
    current = {
        "release_id": 31427573,
        "track_position": "D15",
        "index_in_side": 0,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    assert result is None


def test_advance_none_track_position_returns_none():
    """Cold-start / mid-album case: orchestrator restarted with no prior
    recognition, last_vinyl.track_position is None. The seed builder
    should not call this with None, but be defensive anyway.

    Reviewer should-fix from PR #109 — covers the continuous-mix
    cold-start scenario."""
    current = {
        "release_id": 31427573,
        "side": "D",
        "track_position": None,
        "index_in_side": 0,
    }
    result = nowplaying_main._advance_predicted_position(
        FANTASTIC_PLANET_TRACKS, current
    )
    assert result is None


def test_advance_empty_tracks_list_returns_none():
    """Catalog returned empty tracks (corrupt or missing data) → bail."""
    current = {
        "release_id": 999,
        "side": "A",
        "track_position": "A1",
        "index_in_side": 0,
    }
    result = nowplaying_main._advance_predicted_position([], current)
    assert result is None


# ---- _build_predicted_payload --------------------------------------


def _release_doc(tracks=None):
    """Build a release doc shaped like `discogs_catalog.get_release` returns."""
    return {
        "id": 31427573,
        "artist": "Failure",
        "title": "Fantastic Planet",
        "year": 2024,
        "label": "Failure Records (2)",
        "catno": "FLR010",
        "tracks": tracks if tracks is not None else FANTASTIC_PLANET_TRACKS,
    }


LAST_VINYL_LOCK = {
    "release_id": 31427573,
    "artist": "Failure",
    "album": "Fantastic Planet",
    "title": "Heliotropic",
    "track_position": "D16",
    "side": "D",
    "year": 2024,
    "label": "Failure Records (2)",
    "catno": "FLR010",
    "art_url": "/art/31427573",
    "tracklist": [
        {"position": "D16", "side": "D", "title": "Heliotropic", "duration_seconds": 200},
    ],
}


def test_build_payload_normal():
    """Predicted D17 → payload has Daylight title + Fantastic Planet album-level fields."""
    predicted = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D17",
        "index_in_side": 2,
    }
    with patch.object(
        nowplaying_main.discogs_catalog,
        "get_release",
        return_value=_release_doc(),
    ):
        payload = nowplaying_main._build_predicted_payload(
            LAST_VINYL_LOCK, predicted, source="vinyl"
        )
    assert payload is not None
    assert payload["match_method"] == "predicted"
    assert payload["predicted"] is True
    assert payload["state"] == "PLAYING"
    assert payload["source"] == "vinyl"
    # Album-level fields come from last_vinyl
    assert payload["artist"] == "Failure"
    assert payload["album"] == "Fantastic Planet"
    assert payload["release_id"] == 31427573
    assert payload["year"] == 2024
    assert payload["label"] == "Failure Records (2)"
    assert payload["art_url"] == "/art/31427573"
    # Track-level fields come from the catalog lookup
    assert payload["title"] == "Daylight"
    assert payload["track_position"] == "D17"
    assert payload["side"] == "D"


def test_build_payload_missing_release_returns_none():
    """Catalog miss (deleted release / corrupt DB) returns None."""
    predicted = {
        "release_id": 999999,
        "side": "A",
        "track_position": "A1",
        "index_in_side": 0,
    }
    with patch.object(
        nowplaying_main.discogs_catalog, "get_release", return_value=None,
    ):
        payload = nowplaying_main._build_predicted_payload(
            LAST_VINYL_LOCK, predicted, source="vinyl"
        )
    assert payload is None


def test_build_payload_missing_track_returns_none():
    """Catalog returns a release but the predicted position isn't in the
    tracklist (drift between sync runs). Returns None — caller should
    fall back to NEEDS_ID."""
    predicted = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D99",
        "index_in_side": 9,
    }
    with patch.object(
        nowplaying_main.discogs_catalog,
        "get_release",
        return_value=_release_doc(),
    ):
        payload = nowplaying_main._build_predicted_payload(
            LAST_VINYL_LOCK, predicted, source="vinyl"
        )
    assert payload is None


def test_build_payload_preserves_source():
    """The `source` argument is reflected verbatim — airplay scenario."""
    predicted = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D17",
        "index_in_side": 2,
    }
    with patch.object(
        nowplaying_main.discogs_catalog,
        "get_release",
        return_value=_release_doc(),
    ):
        payload = nowplaying_main._build_predicted_payload(
            LAST_VINYL_LOCK, predicted, source="airplay"
        )
    assert payload is not None
    assert payload["source"] == "airplay"


def test_build_payload_does_not_mutate_last_vinyl():
    """The predicted-payload assembly is read-only against last_vinyl —
    must not pollute the confirmed lock with predicted-track fields."""
    predicted = {
        "release_id": 31427573,
        "side": "D",
        "track_position": "D17",
        "index_in_side": 2,
    }
    snapshot = dict(LAST_VINYL_LOCK)
    with patch.object(
        nowplaying_main.discogs_catalog,
        "get_release",
        return_value=_release_doc(),
    ):
        nowplaying_main._build_predicted_payload(
            LAST_VINYL_LOCK, predicted, source="vinyl"
        )
    assert LAST_VINYL_LOCK == snapshot
