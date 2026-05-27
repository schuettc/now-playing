"""Tests for the `_is_fresh_side_first_track_for_pin` gate's session memory.

The gate must reject pins that land after a *different* track was
recognized since the last audible-edge (needle drop). Without per-session
memory, the gate only inspects the current `state.last_vinyl` snapshot —
which by gate-check time has already been overwritten to the pin's
position by `_apply_pin_to_locked`, so the comparison is always pin==pin
and the gate erroneously passes for mid-side post-predicted-advance pins.

See docs/features/first-track-gate-misses-predicted-advance/.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control._shared import (  # noqa: E402
    _is_fresh_side_first_track_for_pin,
)
from nowplaying.control.pin_track import _schedule_pin_backfill  # noqa: E402
from nowplaying.orchestrator.state import State  # noqa: E402


def _audible_edge(ts_unix: int) -> dict:
    return {
        "type": "audible",
        "ts_iso": datetime.fromtimestamp(ts_unix, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
        "_ts_mono": 0.0,
    }


# ── unit: gate semantics ──────────────────────────────────────────────


def test_gate_passes_when_only_pin_position_recognized_this_session():
    """Real first-track-of-side: audible edge fired, single Shazam confirm
    on the pinned position. Gate should pass and the audible-edge path
    is safe."""
    state = State()
    state.last_vinyl = {"release_id": 100, "track_position": "A1"}
    state.recent_audible_edges = [_audible_edge(int(time.time()) - 10)]
    state.tracks_seen_since_audible_edge = {"A1"}
    assert _is_fresh_side_first_track_for_pin(state, 100, "A1") is True


def test_gate_rejects_when_a_different_track_was_recognized_since_edge():
    """Mid-side post-predicted-advance: audible edge fired, Shazam
    confirmed Pillowhead (B5), predicted-advance moved to Blank (B6),
    user pins B6. By gate-check time `_apply_pin_to_locked` has set
    `last_vinyl["track_position"]` to B6, so the snapshot-only check
    passes — but the session has seen B5, so the gate must reject.
    """
    state = State()
    state.last_vinyl = {"release_id": 100, "track_position": "B6"}
    state.recent_audible_edges = [_audible_edge(int(time.time()) - 90)]
    state.tracks_seen_since_audible_edge = {"B5", "B6"}
    assert _is_fresh_side_first_track_for_pin(state, 100, "B6") is False


def test_gate_passes_when_session_set_empty_and_snapshot_matches():
    """Cold start: no recognitions yet but a `last_vinyl` was seeded from
    another path. The session-set is empty so the new check is permissive —
    the existing edge-count check still gates real safety."""
    state = State()
    state.last_vinyl = {"release_id": 100, "track_position": "A1"}
    state.recent_audible_edges = [_audible_edge(int(time.time()) - 5)]
    state.tracks_seen_since_audible_edge = set()
    assert _is_fresh_side_first_track_for_pin(state, 100, "A1") is True


# ── integration: backfill routes to predicted-transition path ────────


async def _run_schedule(state, *, release_id, track_position, prior_track_position):
    _schedule_pin_backfill(
        state, release_id, track_position, duration_s=200.0,
        prior_track_position=prior_track_position,
    )
    await asyncio.sleep(0)


def test_pin_backfill_uses_predicted_transition_boundary_after_prior_recognition():
    """End-to-end: after predicted-advance, pinning B6 should backfill
    from the last Shazam-confirm (B5 boundary), not the audible-edge.

    Reproduces the live bug from the spec: audible_edge at T-90s
    (needle drop), last Shazam confirm at T-30s (last Pillowhead hit),
    predicted-advance fired in between, user pins B6. The fresh-side
    gate must fail so the predicted-transition path takes over.
    """
    now = int(time.time())
    state = State()
    # By gate-check time, `_apply_pin_to_locked` has already set the
    # last_vinyl track_position to the pin's position (B6).
    state.last_vinyl = {"release_id": 100, "track_position": "B6"}
    state.recent_audible_edges = [_audible_edge(now - 90)]
    state.last_shazam_match_unix_ts = now - 30
    state.tracks_seen_since_audible_edge = {"B5", "B6"}
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
    assert mock_backfill.called, "backfill must still fire via predicted-transition"
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == now - 30, (
        "boundary must come from last_shazam_match_unix_ts, not audible-edge"
    )
