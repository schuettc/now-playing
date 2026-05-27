"""Unit tests for compute_pin_duration — the remaining-time TTL helper.

Covers: known elapsed, unknown elapsed fallback, MIN_PIN_TTL floor, None duration passthrough.
See docs/features/pin-ttl-remaining-time/.
"""
from __future__ import annotations

import calendar
import sys
import time
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.orchestrator.pin import (  # noqa: E402
    MIN_PIN_TTL_S,
    PIN_SAFETY_MARGIN_S,
    compute_pin_duration,
)


# ── fresh-start regression (pin-ttl-35s-phantom-elapsed) ─────────────


def test_fresh_start_b7_segue2_77s_returns_full_duration():
    """B7 Segue 2 (77s) fresh-start pin → TTL = 77, not 42.

    Why: docs/features/pin-ttl-35s-phantom-elapsed/. The old code
    subtracted PIN_FRESH_START_ELAPSED_S (5) + PIN_SAFETY_MARGIN_S (30)
    = 35s from the duration even when the caller explicitly signaled
    fresh-start. For a 77s track this halved pin coverage.
    """
    assert compute_pin_duration(77, None) == 77


def test_fresh_start_b8_dirty_blue_balloons_267s_returns_full_duration():
    """B8 Dirty Blue Balloons (267s) fresh-start pin → TTL = 267, not 232."""
    assert compute_pin_duration(267, None) == 267


def test_fresh_start_b6_blank_339s_returns_full_duration():
    """B6 Blank-style (339s) fresh-start pin → TTL = 339, not 304."""
    assert compute_pin_duration(339, None) == 339


def test_repin_same_track_60s_in_subtracts_elapsed_and_margin():
    """User re-pins a 339s track 60s after track start → TTL = 339-60-30 = 249.

    The legitimate elapsed-time case: caller passed a real
    track_started_at, so we trust it and apply the safety margin.
    """
    now_epoch = 1_000_000.0
    started_iso = _iso_from_epoch(now_epoch - 60)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        result = compute_pin_duration(339, started_iso)
    assert result == 339 - 60 - PIN_SAFETY_MARGIN_S


def _iso_from_epoch(epoch: float) -> str:
    """Format a UTC epoch as the ISO-8601 string the codebase uses."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ── known elapsed ────────────────────────────────────────────────────


def test_known_elapsed_subtracts_from_duration():
    """With a known track start 30s ago, TTL = duration - 30 - SAFETY_MARGIN."""
    now_epoch = 1_000_000.0
    elapsed = 30
    started_iso = _iso_from_epoch(now_epoch - elapsed)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        result = compute_pin_duration(180, started_iso)
    # 180 - 30 - 30 = 120
    assert result == 120


def test_known_elapsed_60s_into_track():
    """User clicks 60s into a 180s track → TTL = max(30, 180-60-30) = 90."""
    now_epoch = 1_000_000.0
    elapsed = 60
    started_iso = _iso_from_epoch(now_epoch - elapsed)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        result = compute_pin_duration(180, started_iso)
    assert result == 90


def test_known_elapsed_10s_into_track():
    """User clicks 10s in — should be just like 10s elapsed, remaining = 140."""
    now_epoch = 1_000_000.0
    elapsed = 10
    started_iso = _iso_from_epoch(now_epoch - elapsed)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        result = compute_pin_duration(200, started_iso)
    # 200 - 10 - 30 = 160
    assert result == 160


# ── unknown elapsed fallback ──────────────────────────────────────────


def test_none_track_started_at_returns_full_duration():
    """When track_started_at is None, TTL = full duration.

    # Why: docs/features/pin-ttl-35s-phantom-elapsed/. The fresh-start
    # signal means the caller doesn't know any elapsed time, so we
    # don't subtract a phantom 35s (old behavior: 5s reaction estimate
    # + 30s safety margin) from a track the user just told us started.
    """
    assert compute_pin_duration(200, None) == 200


def test_unparseable_track_started_at_returns_full_duration():
    """A garbage timestamp falls back to full duration (treat as fresh-start)."""
    assert compute_pin_duration(200, "not-a-date") == 200


# ── MIN_PIN_TTL floor ─────────────────────────────────────────────────


def test_min_pin_ttl_floor_near_end_of_track():
    """When remaining time < MIN_PIN_TTL_S, result is floored at MIN_PIN_TTL_S.

    Scenario: 90s track, user clicks 55s in:
      remaining = 90 - 55 - 30 = 5 → floored to MIN_PIN_TTL_S (30).
    """
    now_epoch = 1_000_000.0
    elapsed = 55
    started_iso = _iso_from_epoch(now_epoch - elapsed)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        result = compute_pin_duration(90, started_iso)
    assert result == MIN_PIN_TTL_S


def test_min_pin_ttl_floor_when_elapsed_exceeds_duration():
    """If elapsed > duration (clock skew / edge), result is MIN_PIN_TTL_S."""
    now_epoch = 1_000_000.0
    elapsed = 200  # more elapsed than the 180s track
    started_iso = _iso_from_epoch(now_epoch - elapsed)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        result = compute_pin_duration(180, started_iso)
    assert result == MIN_PIN_TTL_S


# ── None duration passthrough ─────────────────────────────────────────


def test_none_duration_returns_none():
    """When duration is unknown, compute_pin_duration returns None (TTL disabled)."""
    assert compute_pin_duration(None, None) is None
    assert compute_pin_duration(None, "2026-05-18T12:00:00Z") is None


# ── result is always an int ───────────────────────────────────────────


def test_result_is_int():
    """compute_pin_duration returns int (not float) for use in JSON/TTL checks."""
    assert isinstance(compute_pin_duration(200, None), int)
    now_epoch = 1_000_000.0
    started_iso = _iso_from_epoch(now_epoch - 10)
    with patch("nowplaying.orchestrator.pin.time.time", return_value=now_epoch):
        assert isinstance(compute_pin_duration(200, started_iso), int)
