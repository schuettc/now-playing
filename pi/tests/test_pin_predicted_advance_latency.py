"""Tests for state.last_unmatched_after_match_unix_ts lifecycle.

Covers docs/features/pin-position-ignores-predicted-advance-latency/.

The field tracks the unix wall-clock timestamp of the FIRST Shazam
miss after the most recent Shazam-confirmed match. It is consumed by
the pin endpoint to backdate `initial_track_position_s` and
`track_started_at` when a pin lands after predicted-advance.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.orchestrator.state import State  # noqa: E402


def test_state_initializes_field_to_none():
    s = State()
    assert s.last_unmatched_after_match_unix_ts is None


def test_first_miss_stamps_field_after_shazam_confirm():
    """First unmatched heartbeat after a Shazam confirm stamps the field
    with the current wall-clock time. Subsequent misses do not overwrite.
    """
    from nowplaying.orchestrator._first_miss import (
        mark_first_miss_after_match,
    )
    s = State()
    s.last_shazam_match_unix_ts = 1_700_000_000  # prior confirm exists
    # First miss → stamp
    with patch(
        "nowplaying.orchestrator._first_miss.time.time",
        return_value=1_700_000_030.0,
    ):
        mark_first_miss_after_match(s)
    assert s.last_unmatched_after_match_unix_ts == 1_700_000_030
    # Second miss → don't overwrite (we want the FIRST miss timestamp)
    with patch(
        "nowplaying.orchestrator._first_miss.time.time",
        return_value=1_700_000_045.0,
    ):
        mark_first_miss_after_match(s)
    assert s.last_unmatched_after_match_unix_ts == 1_700_000_030


def test_no_stamp_when_no_prior_shazam_confirm():
    """Without a prior Shazam confirm, there's no 'after-match' to track."""
    from nowplaying.orchestrator._first_miss import (
        mark_first_miss_after_match,
    )
    s = State()
    s.last_shazam_match_unix_ts = None
    mark_first_miss_after_match(s)
    assert s.last_unmatched_after_match_unix_ts is None


def test_field_cleared_when_shazam_match_cleared():
    """Lifecycle: clearing last_shazam_match_unix_ts (idle/source-flip/
    needs-id) also clears last_unmatched_after_match_unix_ts via the
    `clear_first_miss_after_match` helper."""
    from nowplaying.orchestrator._first_miss import clear_first_miss_after_match
    s = State()
    s.last_unmatched_after_match_unix_ts = 1_700_000_030
    clear_first_miss_after_match(s)
    assert s.last_unmatched_after_match_unix_ts is None
