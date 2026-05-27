"""Tests for the silence / level gate on Shazam-only (release_id=None) publishes.

Covers three layers:

  1. `_is_music_level(level_db)` — pure boundary check.
  2. `Orchestrator._shazam_level_gate(level_db, clip_path)` — @staticmethod;
     silence → False, music → True, _instant.wav → True regardless.
  3. `Orchestrator._shazam_only_gate_passes(level_db, clip_path, result)` —
     full gate: level + cross-heartbeat agreement + _instant.wav bypass.
  4. `Orchestrator._publish_shazam_match` (via stub) — Bug A regression:
     gated silence hit must NOT reset state.unmatched_streak.

See docs/features/silence-shazam-false-positive/plan.md.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.main import (  # noqa: E402
    SHAZAM_ONLY_MIN_LEVEL_DB,
    Orchestrator,
    State,
    _is_music_level,
)


# ── helpers ───────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def orch(tmp_path):
    """Minimal Orchestrator stub sufficient for _shazam_only_gate_passes
    and _publish_shazam_match tests. Does not wire bcast / history / llm
    beyond what those two methods touch.
    """
    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = False
    state = State()
    state.unmatched_streak = 3
    state.predicted_position = {"track_position": "A1"}
    state.pending_guess = {"position": "A1", "title": "Guess", "confidence": 0.5, "source": "heuristic"}
    o.state = state
    o.bcast = mock.MagicMock()
    o.bcast.publish = mock.AsyncMock()
    # Stub _anchor_and_publish so _publish_shazam_match doesn't need full state
    o._anchor_and_publish = lambda payload: payload
    return o


def _regular_clip(tmp_path: Path, name: str = "clip_abc.wav") -> Path:
    """Return a path with a regular (non-instant) wav name."""
    p = tmp_path / name
    p.touch()
    return p


def _instant_clip(tmp_path: Path) -> Path:
    """Return a path with the _instant.wav suffix."""
    p = tmp_path / "clip_abc_instant.wav"
    p.touch()
    return p


_SHAZAM_RESULT = {
    "artist": "Adam Harth",
    "title": "Gevlooc",
    "release_id": None,
    "match_method": "shazam",
}

_SILENCE_LEVEL = SHAZAM_ONLY_MIN_LEVEL_DB - 5.0   # well below threshold (-37)
_MUSIC_LEVEL = SHAZAM_ONLY_MIN_LEVEL_DB + 5.0     # well above threshold (-27)


# ── 1. _is_music_level ────────────────────────────────────────────────────


def test_is_music_level_below_threshold_is_false():
    assert _is_music_level(SHAZAM_ONLY_MIN_LEVEL_DB - 0.1) is False


def test_is_music_level_at_threshold_is_true():
    """Boundary is inclusive: >= SHAZAM_ONLY_MIN_LEVEL_DB."""
    assert _is_music_level(SHAZAM_ONLY_MIN_LEVEL_DB) is True


def test_is_music_level_above_threshold_is_true():
    assert _is_music_level(SHAZAM_ONLY_MIN_LEVEL_DB + 0.1) is True


def test_is_music_level_real_playback_level_is_true():
    """Typical loud record sits around -5 dB on the canonical UFO202+preamp."""
    assert _is_music_level(-5.0) is True


def test_is_music_level_deep_silence_is_false():
    """Ambient line-in noise sits at -15 to -16 dB (right at the silence floor),
    well below the music-level threshold."""
    assert _is_music_level(-16.0) is False


# ── 2. Orchestrator._shazam_level_gate ────────────────────────────────────


def test_level_gate_rejects_silent_clip(tmp_path):
    clip = _regular_clip(tmp_path)
    assert Orchestrator._shazam_level_gate(_SILENCE_LEVEL, clip) is False


def test_level_gate_passes_music_level_clip(tmp_path):
    clip = _regular_clip(tmp_path)
    assert Orchestrator._shazam_level_gate(_MUSIC_LEVEL, clip) is True


def test_level_gate_passes_instant_clip_regardless_of_level(tmp_path):
    """_instant.wav suffix bypasses the level check — user explicitly triggered."""
    clip = _instant_clip(tmp_path)
    assert Orchestrator._shazam_level_gate(_SILENCE_LEVEL, clip) is True


def test_level_gate_regular_clip_at_threshold_passes(tmp_path):
    """Exactly at SHAZAM_ONLY_MIN_LEVEL_DB must pass."""
    clip = _regular_clip(tmp_path)
    assert Orchestrator._shazam_level_gate(SHAZAM_ONLY_MIN_LEVEL_DB, clip) is True


# ── 3. Orchestrator._shazam_only_gate_passes ──────────────────────────────


def test_gate_drops_silence_with_wild_shazam_match(orch, tmp_path):
    """Silence-floor clip + Shazam wild match → gate returns False."""
    clip = _regular_clip(tmp_path)
    result = _gate_call(orch, _SILENCE_LEVEL, clip, _SHAZAM_RESULT)
    assert result is False


def test_gate_drops_first_music_level_hit(orch, tmp_path):
    """Music-level clip, first heartbeat of a Shazam-only hit → needs 2 agreements."""
    clip = _regular_clip(tmp_path)
    result = _gate_call(orch, _MUSIC_LEVEL, clip, _SHAZAM_RESULT)
    assert result is False


def test_gate_passes_two_agreements(orch, tmp_path):
    """Music-level + same (artist, title) on two consecutive heartbeats → True."""
    clip = _regular_clip(tmp_path)
    first = _gate_call(orch, _MUSIC_LEVEL, clip, _SHAZAM_RESULT)
    second = _gate_call(orch, _MUSIC_LEVEL, clip, _SHAZAM_RESULT)
    assert first is False   # first hit pending
    assert second is True   # second hit confirmed


def test_gate_drops_two_different_titles(orch, tmp_path):
    """Two different Shazam-only hits (hallucination flip) — never agree."""
    clip = _regular_clip(tmp_path)
    result1 = _gate_call(
        orch, _MUSIC_LEVEL, clip,
        {"artist": "Adam Harth", "title": "Gevlooc", "release_id": None},
    )
    result2 = _gate_call(
        orch, _MUSIC_LEVEL, clip,
        {"artist": "Moby", "title": "All Sides Gone", "release_id": None},
    )
    assert result1 is False
    assert result2 is False


def test_gate_instant_clip_passes_first_attempt(orch, tmp_path):
    """_instant.wav bypasses agreement gate — publishes on the very first hit."""
    clip = _instant_clip(tmp_path)
    result = _gate_call(orch, _MUSIC_LEVEL, clip, _SHAZAM_RESULT)
    assert result is True


def test_gate_instant_clip_records_agreement_for_next_heartbeat(orch, tmp_path):
    """Instant hit is recorded in the window; next regular heartbeat needs only 1 more."""
    instant = _instant_clip(tmp_path)
    regular = _regular_clip(tmp_path)
    # Instant clip fires and records in the agreement window.
    _gate_call(orch, _MUSIC_LEVEL, instant, _SHAZAM_RESULT)
    # Next regular heartbeat has 1 agreement from the instant clip; needs 2 total.
    # After this regular call there are now 2 agreements → should pass.
    result = _gate_call(orch, _MUSIC_LEVEL, regular, _SHAZAM_RESULT)
    assert result is True


def test_gate_drops_empty_artist(orch, tmp_path):
    """Missing artist/title normalises to empty string — gate rejects."""
    clip = _regular_clip(tmp_path)
    result = _gate_call(
        orch, _MUSIC_LEVEL, clip,
        {"artist": "", "title": "Gevlooc", "release_id": None},
    )
    assert result is False


def _gate_call(orch, level_db, clip_path, result):
    """Run _shazam_only_gate_passes synchronously via asyncio.run()."""
    return _run(_async_gate(orch, level_db, clip_path, result))


async def _async_gate(orch, level_db, clip_path, result):
    return orch._shazam_only_gate_passes(level_db, clip_path, result)


# ── 4. _publish_shazam_match Bug-A regression ─────────────────────────────


def test_gated_silence_hit_does_not_reset_unmatched_streak(orch, tmp_path):
    """Bug A regression: a Shazam hallucination on silence that the gate drops
    must NOT reset state.unmatched_streak (or predicted_position / pending_guess).
    """
    clip = _regular_clip(tmp_path)
    initial_streak = orch.state.unmatched_streak
    initial_predicted = orch.state.predicted_position
    initial_guess = orch.state.pending_guess

    # result has release_id=None → goes through _shazam_only_gate_passes
    # level is below threshold → gate returns False → method returns early
    result = dict(_SHAZAM_RESULT)
    _run(_publish(orch, result, clip, _SILENCE_LEVEL))

    assert orch.state.unmatched_streak == initial_streak, (
        "Gated hit must not reset unmatched_streak"
    )
    assert orch.state.predicted_position == initial_predicted, (
        "Gated hit must not clear predicted_position"
    )
    assert orch.state.pending_guess == initial_guess, (
        "Gated hit must not clear pending_guess"
    )
    orch.bcast.publish.assert_not_called()


def test_confirmed_shazam_only_resets_streak_after_two_agreements(orch, tmp_path):
    """A legitimate Shazam-only hit (music level, 2 agreements) DOES reset the streak."""
    clip = _regular_clip(tmp_path)
    result = dict(_SHAZAM_RESULT)

    # First hit: pending, streak unchanged
    _run(_publish(orch, result, clip, _MUSIC_LEVEL))
    assert orch.state.unmatched_streak == 3

    # Second hit: confirmed → streak reset + published
    _run(_publish(orch, result, clip, _MUSIC_LEVEL))
    assert orch.state.unmatched_streak == 0
    orch.bcast.publish.assert_called_once()


async def _publish(orch, result, clip_path, level_db):
    """Thin async wrapper so we can call _publish_shazam_match from _run()."""
    # history.record_play must be stubbed — we don't want DB writes in tests.
    with mock.patch("nowplaying.orchestrator._class.history") as hist:
        hist.record_play = mock.AsyncMock()
        await orch._publish_shazam_match(
            result, "vinyl", clip_path, level_db,
        )
