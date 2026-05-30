"""Identify-path pin TTL uses the audible-edge start for a fresh-side first track.

A user often identifies a track that has already been playing for some
seconds. The identify path used to pin for the full track duration measured
from the click, so a late identify kept the pin alive past the real track
end — freezing predicted-advance and re-poisoning the cohort with the next
track's audio. When the fresh-side-first-track gate passes, the pin's start
must come from the audible edge instead.

See docs/features/advance-on-shazam-quiet-records/.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control import identify  # noqa: E402


def _mk_state() -> MagicMock:
    state = MagicMock()
    state.pending_shazam_only = MagicMock()
    return state


def _run_apply(state, *, rid=12520688, pos="A1", now_iso="2026-05-30T16:35:20Z"):
    matched = {"title": "All Right (Oh Yeah)", "duration_seconds": 190, "side": "A"}
    payload = {"release_id": rid, "track_position": pos}

    async def _go():
        identify._apply_identify_state(state, payload, rid, pos, matched, now_iso)
        await asyncio.sleep(0)  # let any scheduled backfill task start

    asyncio.run(_go())


@patch("nowplaying.control.identify.promotion.schedule_backfill_promotions")
@patch("nowplaying.control.identify._apply_user_track_pin")
@patch("nowplaying.control.identify._audible_edge_unix_ts")
@patch("nowplaying.control.identify._is_fresh_side_first_track_for_pin")
def test_fresh_first_track_pins_from_audible_edge(
    mock_fresh, mock_edge, mock_pin, _mock_backfill,
):
    """First track of a fresh side: pin start comes from the audible edge,
    not the click — so the TTL is elapsed-aware."""
    mock_fresh.return_value = True
    edge_ts = int(time.time()) - 72  # identified 72s into the track
    mock_edge.return_value = edge_ts
    expected_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(edge_ts))

    state = _mk_state()
    _run_apply(state)

    _, kwargs = mock_pin.call_args
    assert kwargs["track_started_at_iso"] == expected_iso
    assert state.track_started_at == expected_iso


@patch("nowplaying.control.identify.promotion.schedule_backfill_promotions")
@patch("nowplaying.control.identify._apply_user_track_pin")
@patch("nowplaying.control.identify._audible_edge_unix_ts")
@patch("nowplaying.control.identify._is_fresh_side_first_track_for_pin")
def test_non_first_track_falls_back_to_now(
    mock_fresh, mock_edge, mock_pin, _mock_backfill,
):
    """Track 2+ on a side (gate fails): no reliable start signal, so the
    pin falls back to click time / full duration as before."""
    mock_fresh.return_value = False
    mock_edge.return_value = int(time.time()) - 72

    state = _mk_state()
    _run_apply(state, pos="A4", now_iso="2026-05-30T16:40:00Z")

    _, kwargs = mock_pin.call_args
    assert kwargs["track_started_at_iso"] is None
    assert state.track_started_at == "2026-05-30T16:40:00Z"
    mock_edge.assert_not_called()  # gate short-circuits the edge lookup


@patch("nowplaying.control.identify.promotion.schedule_backfill_promotions")
@patch("nowplaying.control.identify._apply_user_track_pin")
@patch("nowplaying.control.identify._audible_edge_unix_ts")
@patch("nowplaying.control.identify._is_fresh_side_first_track_for_pin")
def test_fresh_first_track_without_edge_falls_back(
    mock_fresh, mock_edge, mock_pin, _mock_backfill,
):
    """Gate passes but no audible edge is available: fall back to click time
    rather than inventing a start."""
    mock_fresh.return_value = True
    mock_edge.return_value = None

    state = _mk_state()
    _run_apply(state, now_iso="2026-05-30T16:35:20Z")

    _, kwargs = mock_pin.call_args
    assert kwargs["track_started_at_iso"] is None
    assert state.track_started_at == "2026-05-30T16:35:20Z"
