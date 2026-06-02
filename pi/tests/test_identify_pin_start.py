"""Identify-path lock position uses the audible-edge start for a fresh-side
first track.

A user often identifies a track that has already been playing for some
seconds. The lock's hold scales from where the user locked, so for a
fresh-side first track the identify path passes the audible-edge elapsed as
the authoritative ``reliable_position_s`` — without it the lock would outlive
the real track end, freezing predicted-advance and re-poisoning the cohort
with the next track's audio. ``_apply_user_track_pin`` consumes that position
(and sets ``state.track_started_at`` to match).

See docs/features/advance-on-shazam-quiet-records/.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control import identify  # noqa: E402


def _mk_state() -> MagicMock:
    state = MagicMock()
    state.pending_shazam_only = MagicMock()
    return state


def _run_apply(state, *, rid=12520688, pos="A1"):
    matched = {"title": "All Right (Oh Yeah)", "duration_seconds": 190, "side": "A"}
    payload = {"release_id": rid, "track_position": pos}

    async def _go():
        identify._apply_identify_state(state, payload, rid, pos, matched)
        await asyncio.sleep(0)  # let any scheduled backfill task start

    asyncio.run(_go())


@patch("nowplaying.control.identify.promotion.schedule_backfill_promotions")
@patch("nowplaying.control.identify._apply_user_track_pin")
@patch("nowplaying.control.identify._audible_edge_unix_ts")
@patch("nowplaying.control.identify._is_fresh_side_first_track_for_pin")
def test_fresh_first_track_locks_from_audible_edge(
    mock_fresh, mock_edge, mock_pin, _mock_backfill,
):
    """First track of a fresh side: the lock position comes from the audible
    edge elapsed (no age cap), so the hold is position-aware."""
    mock_fresh.return_value = True
    edge_ts = int(time.time()) - 72  # identified 72s into the track
    mock_edge.return_value = edge_ts

    state = _mk_state()
    _run_apply(state)

    _, kwargs = mock_pin.call_args
    assert kwargs["reliable_position_s"] == pytest.approx(72, abs=2)


@patch("nowplaying.control.identify.promotion.schedule_backfill_promotions")
@patch("nowplaying.control.identify._apply_user_track_pin")
@patch("nowplaying.control.identify._audible_edge_unix_ts")
@patch("nowplaying.control.identify._is_fresh_side_first_track_for_pin")
def test_non_first_track_passes_no_reliable_position(
    mock_fresh, mock_edge, mock_pin, _mock_backfill,
):
    """Track 2+ on a side (gate fails): no reliable start signal, so the pin
    receives reliable_position_s=None and falls back to its own estimate."""
    mock_fresh.return_value = False
    mock_edge.return_value = int(time.time()) - 72

    state = _mk_state()
    _run_apply(state, pos="A4")

    _, kwargs = mock_pin.call_args
    assert kwargs["reliable_position_s"] is None
    mock_edge.assert_not_called()  # gate short-circuits the edge lookup


@patch("nowplaying.control.identify.promotion.schedule_backfill_promotions")
@patch("nowplaying.control.identify._apply_user_track_pin")
@patch("nowplaying.control.identify._audible_edge_unix_ts")
@patch("nowplaying.control.identify._is_fresh_side_first_track_for_pin")
def test_fresh_first_track_without_edge_passes_no_reliable_position(
    mock_fresh, mock_edge, mock_pin, _mock_backfill,
):
    """Gate passes but no audible edge is available: pass None rather than
    inventing a start; the pin falls back to its assumed position."""
    mock_fresh.return_value = True
    mock_edge.return_value = None

    state = _mk_state()
    _run_apply(state)

    _, kwargs = mock_pin.call_args
    assert kwargs["reliable_position_s"] is None
