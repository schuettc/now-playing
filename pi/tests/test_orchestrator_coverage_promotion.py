"""Orchestrator-level tests for coverage-driven pin promotion.

Verifies that ``Orchestrator._schedule_coverage_promotion`` fires
``promotion.maybe_promote`` exactly when:
  - a pin is active AND TTL not expired
  - audio is above the silence floor
  - ``promotion.should_promote_for_coverage`` finds a gap

And does NOT fire when:
  - no pin
  - silence floor not met
  - no gap found (should_promote_for_coverage returns False)

Also verifies that the NEW CODE path fires promotion on fingerprint
HIT (not just miss), via ``_try_confirmation_fingerprint``.

Note: these tests use the real ``_schedule_coverage_promotion`` method
but monkeypatch ``promotion.should_promote_for_coverage`` and
``promotion.maybe_promote`` to avoid DB + audio setup.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from nowplaying.orchestrator.streaming_idle import MUSIC_DB
from nowplaying.orchestrator.pin import ANCHOR_TTL_BUFFER_S, PIN_TTL_BUFFER_S
from nowplaying.vinyl import promotion

# Frozen monotonic "now".
_MONO_NOW = 2_000_000.0

# Level that is clearly above the silence floor.
_MUSIC_LEVEL = MUSIC_DB + 10.0
# Level that is clearly below the silence floor.
_SILENCE_LEVEL = MUSIC_DB - 10.0

_FAKE_WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "  # minimal stub — never decoded


def _make_pin(elapsed_s: float = 30.0, duration_s: float = 286.0) -> dict:
    return {
        "release_id": 31427573,
        "track_position": "C10",
        "monotonic_ts": _MONO_NOW - elapsed_s,
        "duration_seconds": duration_s,
    }


@pytest.fixture
def orch(monkeypatch):
    """Minimal Orchestrator with fingerprint_enabled + an active pin."""
    from nowplaying.main import Orchestrator

    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = _MONO_NOW
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = True
    o.state = mock.MagicMock()
    o.state.user_track_pin = _make_pin()
    # Default: no anchor (pin path is the focus of the original PR #183
    # tests). Anchor-driven cases opt in by overriding.
    o.state.fingerprint_anchor = None
    o.state.sonos_source = "vinyl"
    return o


# ── Test A — pin active + gap → maybe_promote is scheduled ───────────────


@pytest.mark.asyncio
async def test_gap_schedules_maybe_promote(orch, monkeypatch):
    """When a gap is found, maybe_promote is dispatched as a task."""
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    promote_mock = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(promotion, "maybe_promote", promote_mock)

    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    assert create_task_mock.called, "expected asyncio.create_task to be called"


# ── Test B — pin active + no gap → maybe_promote NOT called ──────────────


@pytest.mark.asyncio
async def test_no_gap_skips_maybe_promote(orch, monkeypatch):
    """When should_promote_for_coverage returns False, no task is created."""
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: False,
    )
    promote_mock = mock.AsyncMock(return_value=False)
    monkeypatch.setattr(promotion, "maybe_promote", promote_mock)

    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


# ── Test C — no pin → maybe_promote NOT called ───────────────────────────


@pytest.mark.asyncio
async def test_no_pin_skips_promotion(orch, monkeypatch):
    """Without an active pin, no coverage promotion fires."""
    orch.state.user_track_pin = None

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


# ── Test D — silence floor → maybe_promote NOT called ────────────────────


@pytest.mark.asyncio
async def test_silence_floor_skips_promotion(orch, monkeypatch):
    """level_db below MUSIC_DB → no promotion (silence gate)."""
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _SILENCE_LEVEL)

    create_task_mock.assert_not_called()


# ── Test E — fingerprint_enabled=False → maybe_promote NOT called ─────────


@pytest.mark.asyncio
async def test_fingerprint_disabled_skips_promotion(orch, monkeypatch):
    """fingerprint_enabled=False gates all promotion."""
    orch.fingerprint_enabled = False

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


# ── Test F — expired pin → maybe_promote NOT called ───────────────────────


@pytest.mark.asyncio
async def test_expired_pin_skips_promotion(orch, monkeypatch):
    """Pin whose TTL has elapsed does not trigger promotion."""
    # elapsed_s > duration + TTL_BUFFER → expired.
    orch.state.user_track_pin = _make_pin(
        elapsed_s=286.0 + PIN_TTL_BUFFER_S + 60.0,
        duration_s=286.0,
    )

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


# ── Test G — promotion fires on fingerprint HIT (not just miss) ───────────

@pytest.mark.asyncio
async def test_coverage_promotion_fires_on_fingerprint_hit(monkeypatch):
    """_try_confirmation_fingerprint calls _schedule_coverage_promotion on hit.

    This is the key Rule B change: promotion is coverage-driven, not
    outcome-driven. Even a successful fingerprint match must trigger the
    spatial coverage check.
    """
    from nowplaying.main import Orchestrator
    from nowplaying.vinyl import fingerprint

    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = _MONO_NOW
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = True
    o.state = mock.MagicMock()
    o.state.user_track_pin = _make_pin()
    o.state.sonos_source = "vinyl"
    o.state.last_vinyl = {
        "release_id": 31427573,
        "track_position": "C10",
        "title": "Pitiful",
        "artist": "Failure",
        "album": "Fantastic Planet",
        "duration_seconds": 286,
    }
    o.state.fingerprint_anchor = None
    o.bcast = mock.MagicMock()
    o.bcast.publish = mock.AsyncMock()
    o._anchor_and_publish = lambda p: p

    # Stub helpers.
    o._set_fingerprint_anchor = mock.MagicMock()

    # Fake a fingerprint hit above the confidence threshold.
    fake_hit = fingerprint.Hit(
        ref_id=1,
        release_id=31427573,
        track_position="C10",
        hits=200,
        track_position_s=30.0,
    )
    monkeypatch.setattr(
        o, "_lookup_fingerprint_hit",
        mock.AsyncMock(return_value=([fake_hit], _FAKE_WAV)),
    )

    # Stub _build_fingerprint_payload so we don't need a real DB.
    monkeypatch.setattr(
        "nowplaying.orchestrator._class._build_fingerprint_payload",
        lambda *a, **kw: {**o.state.last_vinyl, "match_method": "fingerprint"},
    )

    # history.record_play — stub.
    monkeypatch.setattr(
        "nowplaying.orchestrator._class.history.record_play",
        mock.AsyncMock(),
    )

    # should_promote_for_coverage returns True (gap exists).
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )

    schedule_mock = mock.AsyncMock()
    monkeypatch.setattr(o, "_schedule_coverage_promotion", schedule_mock)

    result = await o._try_confirmation_fingerprint(
        clip_path=Path("/tmp/fake.wav"),
        audio_source_label="vinyl",
        locked_rid=31427573,
        level_db=_MUSIC_LEVEL,
    )

    assert result is True, "fingerprint hit should return True"
    schedule_mock.assert_called_once_with(_FAKE_WAV, _MUSIC_LEVEL)


# ── Tests for anchor-driven coverage promotion (no pin) ───────────────────


def _make_anchor(
    last_pos_s: float = 180.0,
    elapsed_since_match_s: float = 10.0,
    duration_s: float | None = 286.0,
) -> dict:
    """Build a fingerprint anchor dict matching _set_fingerprint_anchor's shape."""
    return {
        "release_id": 31427573,
        "track_position": "C10",
        "monotonic_ts": _MONO_NOW - elapsed_since_match_s,
        "hits": 140,
        "duration_seconds": duration_s,
        "last_matched_ref_position_s": last_pos_s,
    }


@pytest.mark.asyncio
async def test_anchor_active_no_pin_fires_on_gap(orch, monkeypatch):
    """Anchor present, no pin, gap detected → promotion fires.

    Current track_position_s = last_matched_ref_position_s + elapsed_since_match
    = 180 + 10 = 190s. should_promote_for_coverage returns True (gap), so
    promotion is scheduled with the computed position.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor(
        last_pos_s=180.0, elapsed_since_match_s=10.0,
    )

    spy_args: dict = {}

    def fake_should(release_id, track_position, track_position_s, duration_s=None):
        spy_args.update(
            release_id=release_id,
            track_position=track_position,
            track_position_s=track_position_s,
            duration_s=duration_s,
        )
        return True

    monkeypatch.setattr(promotion, "should_promote_for_coverage", fake_should)
    monkeypatch.setattr(promotion, "maybe_promote", mock.AsyncMock(return_value=True))

    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    assert create_task_mock.called
    assert spy_args["release_id"] == 31427573
    assert spy_args["track_position"] == "C10"
    assert spy_args["track_position_s"] == pytest.approx(190.0)


@pytest.mark.asyncio
async def test_anchor_active_no_pin_no_gap_skips(orch, monkeypatch):
    """Anchor present, no pin, no gap (ref already exists near position) → no promotion."""
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor()

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: False,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_expired_anchor_skips_promotion(orch, monkeypatch):
    """Anchor whose TTL has elapsed does not trigger promotion.

    elapsed_since_match > duration + ANCHOR_TTL_BUFFER → anchor expired.
    """
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor(
        last_pos_s=180.0,
        elapsed_since_match_s=286.0 + ANCHOR_TTL_BUFFER_S + 60.0,
        duration_s=286.0,
    )

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_pin_and_anchor_both_active_pin_path_wins(orch, monkeypatch):
    """When both pin and anchor are set, the pin path drives promotion.

    Regression guard: anchor-driven coverage is a fallback, never a
    replacement for pin. The fixture's _make_pin() defaults to
    elapsed_s=30, so track_position_s should be 30 (pin elapsed),
    not 190 (anchor-derived).
    """
    orch.state.user_track_pin = _make_pin(elapsed_s=30.0, duration_s=286.0)
    orch.state.fingerprint_anchor = _make_anchor(
        last_pos_s=180.0, elapsed_since_match_s=10.0,
    )

    spy_args: dict = {}

    def fake_should(release_id, track_position, track_position_s, duration_s=None):
        spy_args.update(track_position_s=track_position_s)
        return True

    monkeypatch.setattr(promotion, "should_promote_for_coverage", fake_should)
    monkeypatch.setattr(promotion, "maybe_promote", mock.AsyncMock(return_value=True))

    with mock.patch("asyncio.create_task"):
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    # Pin path: elapsed_s=30 from _make_pin. Anchor's 190 would be wrong.
    assert spy_args["track_position_s"] == pytest.approx(30.0), (
        "pin path must take precedence — track_position_s should be the pin's elapsed, "
        f"not the anchor's derived position. got {spy_args['track_position_s']}"
    )


@pytest.mark.asyncio
async def test_anchor_missing_last_pos_field_silent_skip(orch, monkeypatch):
    """Defensive: an anchor dict written by an older build that lacks
    last_matched_ref_position_s must NOT crash. Silent skip; next strong
    fingerprint match will refresh the anchor with the new field."""
    orch.state.user_track_pin = None
    anchor = _make_anchor()
    del anchor["last_matched_ref_position_s"]
    orch.state.fingerprint_anchor = anchor

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()


@pytest.mark.asyncio
async def test_anchor_active_silence_floor_skips(orch, monkeypatch):
    """Silence gate applies in anchor mode too — no garbage refs from between-track quiet."""
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = _make_anchor()

    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _SILENCE_LEVEL)

    create_task_mock.assert_not_called()
