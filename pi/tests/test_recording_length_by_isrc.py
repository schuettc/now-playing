from __future__ import annotations
import sys
from pathlib import Path
_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.discovery.musicbrainz_lookup import _pick_recording_length


def test_single_recording():
    assert _pick_recording_length([{"length": 254000, "score": 100}]) == 254

def test_multiple_agree():
    assert _pick_recording_length([
        {"length": 254000, "score": 100},
        {"length": 254000, "score": 100},
    ]) == 254

def test_disagree_same_score_picks_shortest():
    assert _pick_recording_length([
        {"length": 390506, "score": 100},
        {"length": 354040, "score": 100},
    ]) == 354

def test_mode_wins_over_shortest():
    assert _pick_recording_length([
        {"length": 390506, "score": 100},
        {"length": 354040, "score": 100},
        {"length": 354040, "score": 100},
    ]) == 354

def test_score_filter():
    assert _pick_recording_length([
        {"length": 300000, "score": 100},
        {"length": 200000, "score": 50},
    ]) == 300

def test_no_length_returns_none():
    assert _pick_recording_length([{"score": 100}]) is None

def test_empty_returns_none():
    assert _pick_recording_length([]) is None
