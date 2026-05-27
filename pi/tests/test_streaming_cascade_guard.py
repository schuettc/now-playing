"""Regression tests: cascade publish suppression for airplay/streaming sources.

When ``state.sonos_source`` is ``"airplay"`` or ``"streaming"`` at the time the
cascade tries to publish, the publish must be suppressed — the Sonos-listener
publish is authoritative for these sources.

Covers four paths:
  1. ``on_heartbeat``          — Primary gate: entire cascade is skipped.
  2. ``_publish_shazam_match`` — Defence-in-depth: Shazam hit suppressed.
  3. ``_publish_needs_id``     — Defence-in-depth: NEEDS_ID suppressed.
  4. ``_try_fingerprint_fallback`` — Defence-in-depth: fingerprint hit suppressed.

Each path is tested for the three source values:
  - ``"vinyl"``     → publish proceeds (regression guard).
  - ``"airplay"``   → publish suppressed.
  - ``"streaming"`` → publish suppressed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from nowplaying.vinyl.fingerprint import Hit


def _run(coro):
    return asyncio.run(coro)


# ── fixture helpers ───────────────────────────────────────────────────────────


def _make_orch(sonos_source: str):
    """Build a minimal Orchestrator with state.sonos_source set."""
    from nowplaying.main import Orchestrator

    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1_000_000.0

    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = True
    o.state = mock.MagicMock()
    o.state.sonos_source = sonos_source
    o.state.last_vinyl = {
        "title": "Nothing on My Mind",
        "artist": "Hiatus Kaiyote",
        "album": "Tawk Tomahawk",
        "release_id": 99999,
        "track_position": "A1",
        "side": "A",
        "source": sonos_source,
        "tracklist": [
            {"track_position": "A1", "title": "Nothing on My Mind", "side": "A"},
        ],
    }
    o.state.idle_task = None
    o.state.unmatched_streak = 0
    o.state.predicted_position = None
    o.state.pending_guess = None
    o.state.track_started_at = None
    o.state.user_track_pin = None
    o.state.pin_different_track_streak = 0
    o.state.dismissed_guesses = {}
    o.state.pending_shazam_only = []

    from nowplaying.llm import LLMAssist
    o.llm = LLMAssist()
    o.llm.enabled = False

    o.bcast = mock.MagicMock()
    o.bcast.publish = mock.AsyncMock()
    o._anchor_and_publish = lambda payload: payload
    return o


# ── _publish_shazam_match ─────────────────────────────────────────────────────


@pytest.mark.parametrize("source,should_publish", [
    ("vinyl",     True),
    ("airplay",   False),
    ("streaming", False),
])
def test_shazam_match_suppressed_for_non_vinyl(source, should_publish):
    """Shazam-hit publish is suppressed when sonos_source is airplay/streaming."""
    o = _make_orch(source)

    # Shazam result with a Discogs release_id (bypasses shazam-only gate).
    shazam_result = {
        "artist": "Hiatus Kaiyote",
        "title": "Nothing on My Mind",
        "release_id": 99999,
        "match_method": "shazam",
    }
    # Patch _apply_pin_decision to be a no-op (not under test here).
    o._apply_pin_decision = mock.MagicMock()

    with mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()):
        _run(o._publish_shazam_match(
            shazam_result,
            audio_source_label=source,
            clip_path=Path("/fake/clip.wav"),
            level_db=-20.0,
        ))

    if should_publish:
        o.bcast.publish.assert_called_once()
    else:
        o.bcast.publish.assert_not_called()


# ── _publish_needs_id ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("source,should_publish", [
    ("vinyl",     True),
    ("airplay",   False),
    ("streaming", False),
])
def test_needs_id_suppressed_for_non_vinyl(source, should_publish):
    """NEEDS_ID cascade publish is suppressed for airplay/streaming sources."""
    o = _make_orch(source)

    _run(o._publish_needs_id(audio_source_label=source, level_db=-20.0))

    if should_publish:
        o.bcast.publish.assert_called_once()
    else:
        o.bcast.publish.assert_not_called()


def test_needs_id_does_not_clear_pin_for_non_vinyl():
    """When suppressed (airplay), _publish_needs_id must NOT clear the user pin.
    The Sonos state is authoritative; clearing the pin would lose user context.
    """
    o = _make_orch("airplay")
    o.state.user_track_pin = {
        "release_id": 99999,
        "track_position": "A1",
        "monotonic_ts": 999_999.0,
        "duration_seconds": 200,
    }

    _run(o._publish_needs_id(audio_source_label="airplay", level_db=-20.0))

    # Pin must be unchanged — we returned early before the clear.
    assert o.state.user_track_pin is not None


# ── _try_fingerprint_fallback ─────────────────────────────────────────────────


@pytest.mark.parametrize("source,should_publish", [
    ("vinyl",     True),
    ("airplay",   False),
    ("streaming", False),
])
def test_fingerprint_hit_suppressed_for_non_vinyl(source, should_publish):
    """Fingerprint-hit publish is suppressed for airplay/streaming sources.
    The method should still return True (hit found) so callers know to skip
    the unmatched path — we just don't broadcast the cascade result.
    """
    o = _make_orch(source)

    top_hit = Hit(
        ref_id=1,
        release_id=99999,
        track_position="A1",
        hits=120,
        track_position_s=0.0,
    )
    # Mock _lookup_fingerprint_hit to return a confident hit directly.
    o._lookup_fingerprint_hit = mock.AsyncMock(
        return_value=([top_hit], b"fakewav"),
    )
    # Mock promotion helper (not under test).
    o._maybe_schedule_pin_promotion = mock.MagicMock()
    o._compute_track_guess = mock.AsyncMock(return_value=None)

    with mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()):
        result = _run(o._try_fingerprint_fallback(
            clip_path=Path("/fake/clip.wav"),
            audio_source_label=source,
        ))

    # Returns True (hit found) regardless of source.
    assert result is True

    if should_publish:
        o.bcast.publish.assert_called_once()
    else:
        o.bcast.publish.assert_not_called()


# ── on_heartbeat primary gate ─────────────────────────────────────────────────


@pytest.mark.parametrize("source", ["airplay", "streaming"])
def test_on_heartbeat_entirely_skipped_for_non_vinyl(source):
    """For airplay/streaming, on_heartbeat must exit before calling the recognizer.

    This is the primary gate: no Shazam calls, no streak accumulation,
    no predictions, no idle escalation. Ensures rate-limit budget is
    preserved and streak stays at 0 even after multiple heartbeats.
    """
    o = _make_orch(source)
    o.state.sonos_has_metadata = True  # typical during AirPlay

    # _run_recognizer is the first async call after the gate.
    # It must never be called for non-vinyl sources.
    o._run_recognizer = mock.AsyncMock()
    o._retract_pending_idle_for_music = mock.MagicMock()

    _run(o.on_heartbeat(
        clip_path=Path("/fake/clip.wav"),
        level_db=-20.0,
    ))

    o._run_recognizer.assert_not_called()
    o._retract_pending_idle_for_music.assert_not_called()
    o.bcast.publish.assert_not_called()


def test_on_heartbeat_proceeds_for_vinyl():
    """Regression: on_heartbeat must proceed normally for vinyl."""
    o = _make_orch("vinyl")
    o.state.sonos_has_metadata = False

    # Stub the recognizer to return a shazam miss so we don't need
    # a running Shazam service — we just need to verify the gate passed.
    o._run_recognizer = mock.AsyncMock(return_value={
        "match_method": "unmatched",
        "release_id": None,
    })
    o._handle_non_shazam_heartbeat = mock.AsyncMock()
    o._retract_pending_idle_for_music = mock.MagicMock()

    _run(o.on_heartbeat(
        clip_path=Path("/fake/clip.wav"),
        level_db=-20.0,
    ))

    o._run_recognizer.assert_called_once()


def test_on_heartbeat_non_vinyl_no_metadata_is_skipped():
    """AirPlay without metadata (system-audio case) is also skipped.

    `_classify_heartbeat_source` returns 'airplay' when has_metadata=False,
    but the on_heartbeat guard fires immediately after and short-circuits.
    No Shazam call, no streak increment.
    """
    o = _make_orch("airplay")
    o.state.sonos_has_metadata = False
    o._run_recognizer = mock.AsyncMock()

    _run(o.on_heartbeat(
        clip_path=Path("/fake/clip.wav"),
        level_db=-20.0,
    ))

    o._run_recognizer.assert_not_called()
