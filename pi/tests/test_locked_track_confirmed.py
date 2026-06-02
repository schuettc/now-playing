"""A user-locked track must render confirmed (never as a guess) for as long
as it's on screen — even after the lock's hold decays and predicted-advance
re-asserts the same position.

See docs/features/locked-track-stays-confirmed/.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.main import Orchestrator, State  # noqa: E402


def _orch(pin):
    o = Orchestrator.__new__(Orchestrator)
    o.state = State()
    o.state.user_track_pin = pin
    return o


def test_same_position_predicted_renders_confirmed():
    """Predicted publish on the active pin's position → confirmed, not a guess."""
    orch = _orch({"release_id": 12345, "track_position": "D1"})
    payload = {
        "release_id": 12345, "track_position": "D1", "title": "Long Way",
        "match_method": "predicted", "predicted": True,
        "guess": {"position": "D1", "title": "Long Way",
                  "confidence": "medium", "source": "heuristic"},
    }
    orch._keep_locked_track_confirmed(payload)
    assert payload["predicted"] is False
    assert payload["match_method"] == "user-identified"
    assert payload["match_confidence"] == "user"
    assert "guess" not in payload          # not a guess — it's the locked track


def test_case_insensitive_position_match():
    orch = _orch({"release_id": 12345, "track_position": "D1"})
    payload = {"release_id": 12345, "track_position": " d1 ",
               "predicted": True, "match_method": "predicted"}
    orch._keep_locked_track_confirmed(payload)
    assert payload["predicted"] is False


def test_different_position_stays_predicted():
    """A predicted advance to a DIFFERENT track is a real guess — untouched."""
    orch = _orch({"release_id": 12345, "track_position": "D1"})
    payload = {"release_id": 12345, "track_position": "D2",
               "predicted": True, "match_method": "predicted"}
    orch._keep_locked_track_confirmed(payload)
    assert payload["predicted"] is True
    assert payload["match_method"] == "predicted"


def test_no_pin_is_noop():
    orch = _orch(None)
    payload = {"release_id": 12345, "track_position": "D1", "predicted": True}
    orch._keep_locked_track_confirmed(payload)
    assert payload["predicted"] is True


def test_non_predicted_payload_untouched():
    """A confirmed (Shazam/fingerprint) payload is never rewritten."""
    orch = _orch({"release_id": 12345, "track_position": "D1"})
    payload = {"release_id": 12345, "track_position": "D1",
               "match_method": "shazam"}
    orch._keep_locked_track_confirmed(payload)
    assert payload["match_method"] == "shazam"  # left alone (not predicted)
