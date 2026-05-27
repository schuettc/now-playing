"""Regression tests: predicted-advance respects active user pin, fingerprint
anchor, AND the duration guard (Rule A: N-misses alone must never fire
predicted-advance without an affirmative signal that the track ended).

See docs/features/predicted-advance-respects-pin/,
docs/features/blind-anchor-respects-predicted-advance/, and
docs/features/predicted-advance-duration-guard/.

The guard lives in Orchestrator._handle_unmatched_music_level: when
`state.user_track_pin` is set and its TTL has not expired, the method
returns early — no predicted-advance fires and the kiosk stays on the
pinned track.

An identical guard fires for `state.fingerprint_anchor` (set when a blind
fingerprint match exceeds the strong-confidence threshold). Coverage-hole
misses must not flip the kiosk to predicted-advance while the anchor's TTL
is live.

The duration guard (Rule A) fires when elapsed < duration - tolerance and
neither pin nor anchor is active: coverage-gap mid-track misses must not
flip to a wrong predicted track.

Pin scenarios (1–5):
  1. Pin active, TTL not expired → advance suppressed.
  2. Pin active, TTL expired     → advance is permitted.
  3. No pin                      → advance proceeds (regression guard).
  4. Pin active, streak > NEEDS_ID_STREAK → NEEDS_ID suppressed.
  5. Pin expired at high streak  → NEEDS_ID fires.

Fingerprint anchor scenarios (6–9):
  6. Anchor active, TTL not expired              → advance suppressed.
  7. Anchor TTL expired                          → advance is permitted.
  8. No anchor, no pin                           → advance proceeds.
  9. Anchor active, streak > NEEDS_ID_STREAK     → NEEDS_ID suppressed.

Duration guard scenarios (10–12):
  10. N-misses mid-track (elapsed << duration, no pin/anchor)
      → advance suppressed (regression for the 2026-05-18 live bug).
  11. N-misses + elapsed >= duration - tolerance (near end of track)
      → advance fires.
  12. N-misses, no track_started_at (elapsed falls back to seed_back_s,
      which is << long-track duration) → advance suppressed.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from nowplaying.orchestrator.streaming_idle import (
    HEARTBEAT_INTERVAL_S,
    NEEDS_ID_STREAK,
    PREDICTED_ADVANCE_TOLERANCE_S,
)
from nowplaying.orchestrator.pin import ANCHOR_TTL_BUFFER_S, PIN_TTL_BUFFER_S

# Frozen monotonic "now" used by both the pin-builder and the patched loop.
_MONO_NOW = 1_000_000.0  # arbitrary large constant; units = seconds


# ── helpers ──────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _make_pin(elapsed_s: float, duration_s: float = 286) -> dict:
    """Build a user_track_pin dict with a monotonic_ts that puts elapsed
    time at ``elapsed_s`` relative to ``_MONO_NOW``.
    """
    return {
        "release_id": 31427573,
        "track_position": "C10",
        "monotonic_ts": _MONO_NOW - elapsed_s,
        "duration_seconds": duration_s,
    }


def _track_started_at(elapsed_s: float) -> str:
    """Return an ISO-8601 UTC timestamp representing a track that started
    ``elapsed_s`` seconds ago. Used to drive the wall-clock elapsed check
    inside _compute_advance_elapsed_s without mocking datetime.
    """
    anchor = datetime.now(timezone.utc) - timedelta(seconds=elapsed_s)
    return anchor.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def orch(monkeypatch):
    """Orchestrator instance wired with a locked album and a NEEDS_ID_STREAK
    unmatched-streak so the predicted-advance branch is reachable.

    ``asyncio.get_running_loop().time()`` is patched to return ``_MONO_NOW``
    so pin-TTL arithmetic is deterministic without needing a real loop.

    ``_seed_prediction_from_last_vinyl`` and ``_publish_needs_id`` are
    stubbed so we can assert whether they were called.
    """
    from nowplaying.main import Orchestrator

    # Patch asyncio.get_running_loop to return a fake loop whose .time()
    # method returns our frozen constant. The guard calls this once.
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = _MONO_NOW
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = True

    o.state = mock.MagicMock()
    o.state.last_vinyl = {
        "release_id": 31427573,
        "track_position": "C10",
        "side": "C",
        "title": "Pitiful",
        "artist": "Failure",
        "album": "Fantastic Planet",
        "duration_seconds": 286,
    }
    o.state.unmatched_streak = NEEDS_ID_STREAK  # streak just hit the threshold
    o.state.predicted_position = None  # fresh seed path
    o.state.user_track_pin = None      # overridden per-test
    o.state.fingerprint_anchor = None  # overridden per anchor test
    o.state.idle_task = None
    o.state.capture_emit_paused = False
    # Default: simulate track near end (elapsed=260s on a 286s track) so the
    # duration guard (Rule A) does NOT suppress the advance. Tests that want
    # to exercise mid-track suppression override this value explicitly.
    o.state.track_started_at = _track_started_at(elapsed_s=260)

    o.bcast = mock.MagicMock()
    o.bcast.publish = mock.AsyncMock()

    # LLM disabled by default so existing tests are unaffected by the
    # llm-track-change-primary integration (regression guard).
    o.llm = mock.MagicMock()
    o.llm.enabled = False

    # Stub helpers that would reach out to external services.
    o._anchor_and_publish = lambda payload: payload
    o._seed_prediction_from_last_vinyl = mock.AsyncMock(return_value=True)
    o._publish_needs_id = mock.AsyncMock()

    return o


# ── test cases ───────────────────────────────────────────────────────────────


def test_pin_active_suppresses_advance(orch):
    """Pin is set, TTL not expired (30s elapsed on a 286s track).
    Predicted-advance must NOT fire; NEEDS_ID must NOT fire.
    """
    orch.state.user_track_pin = _make_pin(elapsed_s=30)

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_not_called()
    orch._publish_needs_id.assert_not_called()
    orch.bcast.publish.assert_not_called()


def test_pin_ttl_expired_allows_advance(orch):
    """Pin TTL has expired (elapsed > duration + buffer).
    Predicted-advance SHOULD fire.
    """
    duration = 286
    # elapsed = duration + buffer + 5 → definitely expired
    orch.state.user_track_pin = _make_pin(
        elapsed_s=duration + PIN_TTL_BUFFER_S + 5,
        duration_s=duration,
    )

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_awaited_once()


def test_no_pin_allows_advance(orch):
    """No pin at all → predicted-advance proceeds normally."""
    orch.state.user_track_pin = None

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_awaited_once()


def test_pin_active_also_suppresses_needs_id(orch):
    """When streak > NEEDS_ID_STREAK and predicted_position is None
    (the fall-through path that publishes NEEDS_ID), a live pin must
    still suppress the transition.
    """
    # Put streak one above threshold so the seed path is skipped and
    # the NEEDS_ID fall-through is normally reached.
    orch.state.unmatched_streak = NEEDS_ID_STREAK + 1
    orch.state.predicted_position = None
    orch.state.user_track_pin = _make_pin(elapsed_s=30)

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._publish_needs_id.assert_not_called()
    orch._seed_prediction_from_last_vinyl.assert_not_called()


def test_pin_expired_high_streak_triggers_needs_id(orch):
    """Regression: pin expires when streak is already > NEEDS_ID_STREAK.

    Before the fix, the `streak > NEEDS_ID_STREAK` block would return
    early without calling `_publish_needs_id`, leaving the kiosk stuck
    on the expired pin indefinitely.  After the fix that block only
    skips the transition when `user_track_pin is None` (i.e. we
    already transitioned normally).
    """
    duration = 286
    orch.state.unmatched_streak = NEEDS_ID_STREAK + 3  # streak has grown past threshold
    orch.state.predicted_position = None
    # Pin is expired — elapsed > duration + buffer
    orch.state.user_track_pin = _make_pin(
        elapsed_s=duration + PIN_TTL_BUFFER_S + 5,
        duration_s=duration,
    )

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._publish_needs_id.assert_awaited_once()
    orch._seed_prediction_from_last_vinyl.assert_not_called()


# ── fingerprint anchor guard scenarios (6–9) ─────────────────────────────────


def _make_anchor(elapsed_s: float, duration_s: float = 286, hits: int = 115) -> dict:
    """Build a fingerprint_anchor dict with a monotonic_ts that puts elapsed
    time at ``elapsed_s`` relative to ``_MONO_NOW``.
    """
    return {
        "release_id": 31427573,
        "track_position": "C10",
        "monotonic_ts": _MONO_NOW - elapsed_s,
        "hits": hits,
        "duration_seconds": duration_s,
    }


def test_fingerprint_anchor_suppresses_advance(orch):
    """Fingerprint anchor set, TTL not expired (30s elapsed on 286s track).
    Predicted-advance must NOT fire; NEEDS_ID must NOT fire.
    Live scenario: blind matched Pitiful at 115 hits → 2 miss heartbeats →
    kiosk must stay on Pitiful.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor(elapsed_s=30)

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_not_called()
    orch._publish_needs_id.assert_not_called()
    orch.bcast.publish.assert_not_called()


def test_fingerprint_anchor_ttl_expired_allows_advance(orch):
    """Anchor TTL has expired (elapsed > duration + buffer).
    Predicted-advance SHOULD fire.
    """
    duration = 286
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor(
        elapsed_s=duration + ANCHOR_TTL_BUFFER_S + 5,
        duration_s=duration,
    )

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_awaited_once()


def test_no_anchor_no_pin_allows_advance(orch):
    """No anchor and no pin → predicted-advance proceeds normally.
    Regression guard: the new anchor check must not break the default flow.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_awaited_once()


def test_fingerprint_anchor_also_suppresses_needs_id(orch):
    """When streak > NEEDS_ID_STREAK and predicted_position is None
    (the fall-through path that publishes NEEDS_ID), a live anchor must
    still suppress the transition.
    """
    orch.state.unmatched_streak = NEEDS_ID_STREAK + 1
    orch.state.predicted_position = None
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor(elapsed_s=30)

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._publish_needs_id.assert_not_called()
    orch._seed_prediction_from_last_vinyl.assert_not_called()


# ── duration guard scenarios (10–12) — Rule A ────────────────────────────────


def test_duration_guard_suppresses_mid_track_advance(orch):
    """Regression for the 2026-05-18 live bug: Pitiful (286s), fp_refs
    cluster at 8–194s. At t≈75s the refs run out, fingerprint starts
    missing, streak hits NEEDS_ID_STREAK. Without the guard the kiosk
    flipped to Leo (wrong track). With the guard it must fall through to
    NEEDS_ID instead.

    elapsed=75s < duration(286s) - tolerance(30s) = 256s → suppressed.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    # Pitiful has duration_seconds=286 (already in fixture's last_vinyl)
    orch.state.track_started_at = _track_started_at(elapsed_s=75)

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_not_called()
    # Should fall through to NEEDS_ID instead of flipping to wrong track.
    orch._publish_needs_id.assert_awaited_once()


def test_duration_guard_allows_near_end_of_track_advance(orch):
    """Near the end of the track: elapsed >= duration - tolerance.
    The guard must NOT suppress — predicted-advance should fire.

    elapsed=260s >= duration(286s) - tolerance(30s) = 256s → allowed.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.track_started_at = _track_started_at(elapsed_s=260)

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    orch._seed_prediction_from_last_vinyl.assert_awaited_once()
    orch._publish_needs_id.assert_not_called()


def test_duration_guard_no_track_started_at_suppresses_long_track(orch):
    """When track_started_at is None, _compute_advance_elapsed_s falls back
    to seed_back_s = NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S (30s by default).
    For a long track (286s), 30s elapsed is well inside the guard threshold
    (286 - 30 = 256s), so the advance must be suppressed.

    This ensures that missing track-start anchors don't become a loophole
    that defeats the duration guard.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.track_started_at = None  # no anchor available

    _run(orch._handle_unmatched_music_level("vinyl", -20.0))

    seed_back_s = NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S  # 30s fallback
    # 30s fallback << 256s threshold — guard should fire
    orch._seed_prediction_from_last_vinyl.assert_not_called()
    orch._publish_needs_id.assert_awaited_once()
