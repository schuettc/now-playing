"""Tests for `_schedule_pin_backfill`'s boundary using max(shazam, pin).

When a user pins track B6, then a few minutes later pins B7 on the same
album lock, the backfill boundary for B7 must anchor to the prior pin's
timestamp (B6 pin), not the older `last_shazam_match_unix_ts` which may
predate B6 itself.

Otherwise the B7 backfill window spans all of B6's audio and mis-labels
it as B7. The cross-cohort guard catches most cases but ambiguous audio
sneaks through with track positions exceeding the new track's duration.

See docs/features/backfill-boundary-uses-stale-shazam-ts/.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control.pin_track import _schedule_pin_backfill  # noqa: E402


def _mk_state(
    *,
    release_id: int = 100,
    prior_track_position: str = "B6",
    last_shazam_match_unix_ts: int | None = None,
    last_pin_unix_ts: int | None = None,
):
    state = MagicMock()
    state.last_vinyl = {
        "release_id": release_id,
        "track_position": prior_track_position,
    }
    state.last_shazam_match_unix_ts = last_shazam_match_unix_ts
    state.last_pin_unix_ts = last_pin_unix_ts
    state.recent_audible_edges = []
    state.fingerprint_anchor = None
    return state


async def _run_schedule(
    state, *, release_id, track_position, prior_track_position,
    prior_pin_unix_ts=None,
    prior_pin_duration_seconds=None,
):
    _schedule_pin_backfill(
        state, release_id, track_position, duration_s=77.0,
        prior_track_position=prior_track_position,
        prior_pin_unix_ts=prior_pin_unix_ts,
        prior_pin_duration_seconds=prior_pin_duration_seconds,
    )
    await asyncio.sleep(0)


def test_boundary_uses_prior_pin_when_more_recent_than_shazam():
    """Chained pin: B6 pinned 100s ago, last Shazam was 1000s ago →
    boundary must be the B6 pin timestamp, not the stale Shazam ts."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B6",
        last_shazam_match_unix_ts=now - 1000,
        last_pin_unix_ts=now - 100,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B7",
            prior_track_position="B6",
            prior_pin_unix_ts=now - 100,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 100


def test_boundary_uses_shazam_when_more_recent_than_prior_pin():
    """Shazam ran more recently than the last pin → use Shazam ts."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B6",
        last_shazam_match_unix_ts=now - 30,
        last_pin_unix_ts=now - 200,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B7",
            prior_track_position="B6",
            prior_pin_unix_ts=now - 200,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 30


def test_boundary_uses_pin_alone_when_shazam_is_none():
    """Shazam ts is None (e.g. cleared) but a recent pin exists → use pin
    ts. Previously this returned None and skipped backfill entirely."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B6",
        last_shazam_match_unix_ts=None,
        last_pin_unix_ts=now - 60,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B7",
            prior_track_position="B6",
            prior_pin_unix_ts=now - 60,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 60


def test_first_pin_no_prior_pin_falls_back_to_shazam():
    """Regression guard for `2c4e440` behavior: when last_pin_unix_ts is
    None (first pin after audio started), boundary still anchors to
    last_shazam_match_unix_ts."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=now - 30,
        last_pin_unix_ts=None,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B6",
            prior_track_position="B5",
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 30


def test_both_boundaries_none_skips_backfill():
    """No Shazam history and no prior pin → still skip (no boundary)."""
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=None,
        last_pin_unix_ts=None,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=0),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B6",
            prior_track_position="B5",
        ))
    assert not mock_backfill.called


# Why: prior `test_stale_shazam_and_stale_pin_both_outside_window_skips`
# asserted the 300s cap rejected old boundaries. The cap was removed
# (see docs/features/remove-predicted-transition-max-age/) — old
# boundaries are now accepted because idle cleanup clears state after
# ~2 min of silence and cross-cohort guard catches mis-labeled clips.
# Test repurposed to assert old boundaries ARE accepted (max chosen).
def test_stale_shazam_and_stale_pin_both_accepted_after_cap_removal():
    """Both timestamps older than the (removed) 300s cap → backfill still
    fires, anchored to the more recent of the two. The cross-cohort
    guard and idle cleanup are the protection layer now."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=now - 1000,
        last_pin_unix_ts=now - 800,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B6",
            prior_track_position="B5",
            prior_pin_unix_ts=now - 800,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 800


def test_chained_pin_backfill_skips_prior_track_audio_window():
    """Backfill from a B6→B7 chained pin must lower-bound the window at
    `prior_pin_ts + prior_pin_duration_seconds`, not the prior pin ts
    itself. Earlier clips contain B6 audio, not B7.

    See docs/features/backfill-window-assumes-boundary-is-track-start/.
    """
    now = int(time.time())
    # B6 pinned 300s ago, duration=339s → B6 expected end at now-300+339=now+39
    # (i.e. B6 should still be playing — but we tighten to the projected end).
    # More realistic: B6 pinned 300s ago, duration=232s → B6 ended at now-68.
    prior_pin_ts = now - 300
    prior_pin_duration = 232
    state = _mk_state(
        prior_track_position="B6",
        last_shazam_match_unix_ts=now - 1000,
        last_pin_unix_ts=prior_pin_ts,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B7",
            prior_track_position="B6",
            prior_pin_unix_ts=prior_pin_ts,
            prior_pin_duration_seconds=prior_pin_duration,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    # Tightened lower bound = prior_pin_ts + prior_pin_duration_seconds
    assert kwargs["audible_edge_unix_ts"] == prior_pin_ts + prior_pin_duration


def test_chained_pin_backfill_no_tightening_when_prior_duration_unknown():
    """When prior pin has no duration (no catalog data), behavior matches
    today: boundary stays at the prior pin timestamp, no tightening."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B6",
        last_shazam_match_unix_ts=now - 1000,
        last_pin_unix_ts=now - 100,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="B7",
            prior_track_position="B6",
            prior_pin_unix_ts=now - 100,
            prior_pin_duration_seconds=None,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 100


def test_first_pin_in_session_no_tightening_audible_edge_unchanged():
    """First pin in session (no prior pin, no shazam ts) on fresh side:
    audible-edge path is unchanged — no tightening applies because
    prior_pin_unix_ts is None.
    """
    now = int(time.time())
    state = _mk_state(
        prior_track_position="A1",
        last_shazam_match_unix_ts=now - 30,
        last_pin_unix_ts=None,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state, release_id=100, track_position="A2",
            prior_track_position="A1",
            prior_pin_unix_ts=None,
            prior_pin_duration_seconds=None,
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    # Behavior unchanged: shazam ts used directly.
    assert kwargs["audible_edge_unix_ts"] == now - 30


def test_apply_user_track_pin_stamps_last_pin_unix_ts():
    """`_apply_user_track_pin` must stamp `state.last_pin_unix_ts` so
    the next pin sees the prior pin's timestamp as a boundary."""
    from nowplaying.control._shared import _apply_user_track_pin

    state = MagicMock()
    state.last_vinyl = {"release_id": 100, "track_position": "B6"}
    state.fingerprint_anchor = None
    state.recent_audible_edges = []
    state.last_pin_unix_ts = None
    matched = {"position": "B6", "title": "Blank", "duration_seconds": 339}

    async def _run():
        before = int(time.time())
        _apply_user_track_pin(state, 100, "B6", matched)
        after = int(time.time())
        assert state.last_pin_unix_ts is not None
        assert before <= state.last_pin_unix_ts <= after

    asyncio.run(_run())
