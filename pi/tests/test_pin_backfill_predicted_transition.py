"""Tests for `_schedule_pin_backfill`'s predicted-transition path.

When a user pins a track that differs from the prior locked track AND a
recent Shazam-confirmed match exists on the prior track, backfill should
fire using `state.last_shazam_match_unix_ts` as the lower bound — even
when the fresh-side-first-track gate fails (mid-side pin).

See docs/features/pin-backfill-from-predicted-transition/.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control.pin_track import _schedule_pin_backfill  # noqa: E402


def _mk_state(
    *,
    release_id: int = 100,
    prior_track_position: str = "B5",
    last_shazam_match_unix_ts: int | None = None,
    recent_audible_edges: list[dict] | None = None,
    fingerprint_anchor: dict | None = None,
):
    """Build a state stub for backfill scheduling tests."""
    state = MagicMock()
    state.last_vinyl = {
        "release_id": release_id,
        "track_position": prior_track_position,
    }
    state.last_shazam_match_unix_ts = last_shazam_match_unix_ts
    state.recent_audible_edges = recent_audible_edges or []
    state.fingerprint_anchor = fingerprint_anchor
    return state


async def _run_schedule(state, *, release_id, track_position, prior_track_position):
    """Invoke _schedule_pin_backfill from inside a running loop, then drain.

    The helper schedules an asyncio.create_task; we need a running loop and
    a chance for the task to run.
    """
    _schedule_pin_backfill(
        state, release_id, track_position, duration_s=200.0,
        prior_track_position=prior_track_position,
    )
    # Yield so the scheduled task runs (it awaits the mocked coroutine).
    await asyncio.sleep(0)


# ── predicted-transition path: happy path ────────────────────────────


def test_predicted_transition_backfill_fires_when_pin_differs_and_shazam_recent():
    """Pin on B6 lands after prior B5 was Shazam-confirmed 30s ago →
    schedule_backfill_promotions should fire with last_shazam_match_unix_ts
    as the lower bound."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=now - 30,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=2),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state,
            release_id=100,
            track_position="B6",
            prior_track_position="B5",
        ))
    assert mock_backfill.called, "expected backfill to fire on predicted transition"
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["release_id"] == 100
    assert kwargs["track_position"] == "B6"
    assert kwargs["audible_edge_unix_ts"] == now - 30
    assert kwargs["pin_unix_ts"] >= now


# ── skip cases ───────────────────────────────────────────────────────


def test_no_backfill_when_pin_same_track_as_prior():
    """Pin re-confirms the same track → no backfill (no transition)."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=now - 30,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=0),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state,
            release_id=100,
            track_position="B5",
            prior_track_position="B5",
        ))
    assert not mock_backfill.called


def test_no_backfill_when_no_recent_shazam_history():
    """Pin transitions track but `last_shazam_match_unix_ts` is None →
    no backfill (no reliable boundary)."""
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=None,
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=0),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state,
            release_id=100,
            track_position="B6",
            prior_track_position="B5",
        ))
    assert not mock_backfill.called


# Why: prior `test_no_backfill_when_shazam_match_too_old` asserted the
# 300s cap rejected old boundaries. The cap was removed (see
# docs/features/remove-predicted-transition-max-age/) because it
# rejected valid chained-pin boundaries on tracks >5min, and idle
# cleanup + cross-cohort guard already handle stale-boundary risk.
# Test repurposed to assert old boundaries ARE now accepted.
def test_backfill_accepts_boundary_older_than_old_300s_cap():
    """A boundary 600s old (was rejected by the removed 300s cap) must
    now produce a valid backfill window. Long tracks (e.g. B6 Blank at
    339s catalog) generated B6→B7 pin gaps exceeding 300s, losing
    inter-pin coverage under the old cap."""
    now = int(time.time())
    state = _mk_state(
        prior_track_position="B5",
        last_shazam_match_unix_ts=now - 600,  # 10 minutes ago
    )
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state,
            release_id=100,
            track_position="B6",
            prior_track_position="B5",
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 600


# ── fresh-side-first-track path still takes precedence ──────────────


def test_fresh_side_first_track_path_still_works_without_shazam_history():
    """When the fresh-side gate passes, backfill fires via the
    audible-edge path even with no Shazam history (regression guard)."""
    from datetime import datetime, timezone
    now = int(time.time())
    edge_iso = datetime.fromtimestamp(now - 20, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )
    # Fresh-side gate requires: last_vinyl matches pin, exactly 1 audible
    # edge, no silent edges.
    state = _mk_state(
        prior_track_position="A1",
        last_shazam_match_unix_ts=None,
        recent_audible_edges=[
            {"type": "audible", "ts_iso": edge_iso},
        ],
    )
    state.last_vinyl = {"release_id": 100, "track_position": "A1"}
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=1),
    ) as mock_backfill:
        asyncio.run(_run_schedule(
            state,
            release_id=100,
            track_position="A1",
            prior_track_position="A1",
        ))
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 20
