"""Tests for `enrich_guess_contract` — the backend guess contract
(confidence / expires_in_s / confirmable) stamped onto a payload's guess.

Epic consolidate-guess-confidence-lifetime / C2. See
docs/features/guess-payload-contract/.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.orchestrator.prediction import enrich_guess_contract  # noqa: E402


def _iso(secs_ago: float) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(seconds=secs_ago))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def test_enrich_no_guess_is_noop():
    payload = {"track_position": "A1"}
    enrich_guess_contract(payload)
    assert "guess" not in payload


def test_enrich_predicted_track_confirmable_with_remaining_and_high_confidence():
    # 200s track, 20s in → 180s left, well before the 45s decay window → high.
    payload = {
        "match_method": "predicted",
        "duration_seconds": 200,
        "track_started_at": _iso(20),
        "guess": {"position": "A2", "title": "x", "confidence": "medium", "source": "heuristic"},
    }
    enrich_guess_contract(payload)
    g = payload["guess"]
    assert g["confirmable"] is True          # predicted now-playing → offer confirm
    assert 178 <= g["expires_in_s"] <= 180   # ~200-20, tolerant of test wall-clock
    assert g["confidence"] == "high"


def test_enrich_confirmed_track_is_not_confirmable():
    # A Shazam-confirmed now-playing with a (matching) guess attached is a
    # passive hint, not a confirm prompt.
    payload = {
        "match_method": "shazam",
        "duration_seconds": 200,
        "track_started_at": _iso(10),
        "guess": {"position": "A1"},
    }
    enrich_guess_contract(payload)
    assert payload["guess"]["confirmable"] is False


def test_enrich_near_end_low_confidence():
    payload = {
        "match_method": "predicted",
        "duration_seconds": 200,
        "track_started_at": _iso(195),
        "guess": {"position": "A2"},
    }
    enrich_guess_contract(payload)
    assert 4 <= payload["guess"]["expires_in_s"] <= 6   # ~200-195
    assert payload["guess"]["confidence"] == "low"


def test_enrich_unknown_duration_is_open_ended():
    # NEEDS_ID-style payload: no duration, no match_method.
    payload = {"match_method": None, "guess": {"position": "A1", "confidence": "low"}}
    enrich_guess_contract(payload)
    g = payload["guess"]
    assert g["expires_in_s"] is None
    assert g["confirmable"] is True           # None not a confirmed method
    assert g["confidence"] == "low"           # left at source value, no duration
