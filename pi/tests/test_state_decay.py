"""Unit tests for the state-decay-when-stale feature.

Tests cover every branch of ``Orchestrator._check_state_decay``:
  - no-op when last_vinyl is None
  - no-op when confidence stamp is None (unstamped path, e.g. Sonos)
  - no-op when age < STATE_DECAY_S
  - decay fires when age >= STATE_DECAY_S, no pin, no anchor
  - decay suppressed by active (non-expired) user_track_pin
  - decay suppressed by active (non-expired) fingerprint_anchor
  - decay fires when pin is present but TTL-expired
  - decay fires when anchor is present but TTL-expired
  - streak is reset to 0 when decay fires
  - confidence stamp is cleared when decay fires
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nowplaying.orchestrator._class import Orchestrator
from nowplaying.orchestrator.state import State
from nowplaying.orchestrator.streaming_idle import NEEDS_ID_STREAK, STATE_DECAY_S


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**kwargs) -> State:
    s = State()
    s.sonos_source = "vinyl"
    s.last_vinyl = {"title": "Pitiful", "release_id": 12345}
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_orchestrator(state: State) -> Orchestrator:
    bcast = AsyncMock()
    bcast.publish = AsyncMock()
    return Orchestrator(
        state=state,
        bcast=bcast,
        sonos_coord=None,
        stop=asyncio.Event(),
        llm=AsyncMock(enabled=False),
        fingerprint_enabled=False,
    )


# Monotonic "now" used in all tests — a large arbitrary value so age
# calculations stay simple.
_NOW = 10_000.0


async def _run_check(state: State, stamp_age_s: float | None) -> bool:
    """Set up state.last_vinyl_confidence_set_at relative to _NOW and run the
    decay check under a patched asyncio loop time."""
    if stamp_age_s is not None:
        state.last_vinyl_confidence_set_at = _NOW - stamp_age_s
    orch = _make_orchestrator(state)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        return await orch._check_state_decay("vinyl", -40.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_decay_if_no_last_vinyl() -> None:
    """last_vinyl is None — nothing to decay."""
    state = _make_state(last_vinyl=None)
    result = await _run_check(state, stamp_age_s=None)
    assert result is False


@pytest.mark.asyncio
async def test_no_decay_if_no_stamp() -> None:
    """last_vinyl is set but confidence stamp is None (e.g. Sonos airplay path)."""
    state = _make_state()
    # last_vinyl_confidence_set_at remains None
    state.last_vinyl_confidence_set_at = None
    result = await _run_check(state, stamp_age_s=None)
    assert result is False


@pytest.mark.asyncio
async def test_no_decay_before_ttl() -> None:
    """Stamp is recent (20 s < STATE_DECAY_S=45 s) — no decay."""
    state = _make_state()
    result = await _run_check(state, stamp_age_s=20.0)
    assert result is False


@pytest.mark.asyncio
async def test_decay_fires_after_ttl() -> None:
    """Stamp is 50 s old, no pin, no anchor → decay fires, returns True."""
    state = _make_state()
    result = await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert result is True


@pytest.mark.asyncio
async def test_decay_clears_state() -> None:
    """When decay fires, last_vinyl_confidence_set_at is cleared and
    unmatched_streak is reset to 0."""
    state = _make_state(unmatched_streak=3)
    await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert state.last_vinyl_confidence_set_at is None
    assert state.unmatched_streak == 0


@pytest.mark.asyncio
async def test_no_decay_with_active_pin() -> None:
    """Active pin (no duration → never TTL-expired) suppresses decay."""
    state = _make_state()
    state.user_track_pin = {
        "release_id": 12345,
        "track_position": "A1",
        "monotonic_ts": _NOW - 10,
        "duration_seconds": None,  # no TTL — pin never expires
    }
    result = await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert result is False


@pytest.mark.asyncio
async def test_no_decay_with_active_anchor() -> None:
    """Active fingerprint anchor (no duration → never TTL-expired) suppresses decay."""
    state = _make_state()
    state.fingerprint_anchor = {
        "release_id": 12345,
        "track_position": "A1",
        "monotonic_ts": _NOW - 10,
        "hits": 90,
        "duration_seconds": None,  # no TTL — anchor never expires
    }
    result = await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert result is False


@pytest.mark.asyncio
async def test_pin_ttl_expiry_refreshes_stamp_and_clears_pin() -> None:
    """When a pin TTL has just expired, decay must NOT fire — instead the
    confidence stamp is refreshed and the pin is cleared, giving the next
    heartbeat a clean window to run predicted-advance.

    See docs/features/pin-expiry-flashes-needs-id/.
    """
    state = _make_state()
    # PIN_TTL_BUFFER_S = 0; set duration_seconds=5, elapsed=25 → expired
    state.user_track_pin = {
        "release_id": 12345,
        "track_position": "A1",
        "monotonic_ts": _NOW - 25,
        "duration_seconds": 5,
    }
    result = await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert result is False
    assert state.user_track_pin is None
    assert state.last_vinyl_confidence_set_at == _NOW


@pytest.mark.asyncio
async def test_pin_ttl_expiry_resets_streak_for_predicted_advance() -> None:
    """When a pin TTL expires, _check_state_decay must reset unmatched_streak
    so the next heartbeat (which increments it by 1) lands on
    NEEDS_ID_STREAK and triggers the seed-prediction branch in
    _handle_unmatched_music_level. Without this reset the streak stays at
    its pin-window value (20+) and the seed branch (gated on
    streak == NEEDS_ID_STREAK) never fires — kiosk gets stuck in
    "(still in NEEDS_ID)".

    See docs/features/pin-clearance-no-predicted-advance-at-high-streak/.
    """
    state = _make_state(unmatched_streak=22)
    state.user_track_pin = {
        "release_id": 12345,
        "track_position": "B6",
        "monotonic_ts": _NOW - 100,
        "duration_seconds": 60,  # expired
    }
    result = await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert result is False
    assert state.user_track_pin is None
    assert state.last_vinyl_confidence_set_at == _NOW
    # The fix: streak is reset to NEEDS_ID_STREAK - 1 so the next
    # heartbeat's increment lands exactly on NEEDS_ID_STREAK.
    assert state.unmatched_streak == NEEDS_ID_STREAK - 1


@pytest.mark.asyncio
async def test_pin_ttl_expiry_then_next_heartbeat_fires_predicted_advance() -> None:
    """Integration: after pin TTL expiry resets the streak, the next
    unmatched music-level heartbeat must reach the seed-prediction branch
    (streak == NEEDS_ID_STREAK after increment) and invoke
    _seed_prediction_from_last_vinyl.
    """
    state = _make_state(unmatched_streak=22)
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "B6",
        "side": "B",
        "title": "Blank",
        "duration_seconds": 339,
    }
    # track_started_at way back so the duration guard passes
    # (elapsed >= duration - tolerance).
    from datetime import datetime, timedelta, timezone
    state.track_started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=400)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    state.user_track_pin = {
        "release_id": 12345,
        "track_position": "B6",
        "monotonic_ts": _NOW - 100,
        "duration_seconds": 60,  # expired
    }
    state.last_vinyl_confidence_set_at = _NOW - (STATE_DECAY_S + 5)
    orch = _make_orchestrator(state)
    orch._seed_prediction_from_last_vinyl = AsyncMock(return_value=True)

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        # Heartbeat 1: pin TTL expiry — _check_state_decay resets streak.
        fired = await orch._check_state_decay("vinyl", -20.0)
        assert fired is False
        assert state.user_track_pin is None
        assert state.unmatched_streak == NEEDS_ID_STREAK - 1
        # Heartbeat 2: increment streak, route to music-level handler.
        state.unmatched_streak += 1
        assert state.unmatched_streak == NEEDS_ID_STREAK
        await orch._handle_unmatched_music_level("vinyl", -20.0)

    orch._seed_prediction_from_last_vinyl.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_needs_id_flash_on_pin_expiry_between_tracks() -> None:
    """End-to-end: when a user-pinned track's TTL expires at end of track,
    the immediate next heartbeat must NOT publish NEEDS_ID. The pin-expiry
    branch refreshes the stamp and clears the pin, so state-decay holds and
    the unmatched-streak path runs (which gives predicted-advance time to
    fire on a later heartbeat).
    """
    state = _make_state()
    # Stamp is "as old as the pin TTL" — matches the live bug scenario where
    # the stamp was set at pin time and is now > STATE_DECAY_S.
    state.user_track_pin = {
        "release_id": 12345,
        "track_position": "B7",
        "monotonic_ts": _NOW - 100,
        "duration_seconds": 77,  # 100 > 77 (PIN_TTL_BUFFER_S=0) → expired
    }
    orch = _make_orchestrator(state)
    state.last_vinyl_confidence_set_at = _NOW - 99.0  # mirrors live log
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        fired = await orch._check_state_decay("vinyl", -40.0)
    assert fired is False, "state-decay must not fire on the pin-expiry heartbeat"
    orch.bcast.publish.assert_not_called()
    assert state.user_track_pin is None
    assert state.last_vinyl_confidence_set_at == _NOW


@pytest.mark.asyncio
async def test_decay_fires_when_anchor_ttl_expired() -> None:
    """Anchor present but TTL expired → decay proceeds."""
    state = _make_state()
    # ANCHOR_TTL_BUFFER_S = 15; set duration_seconds=5, elapsed=25 → expired
    state.fingerprint_anchor = {
        "release_id": 12345,
        "track_position": "A1",
        "monotonic_ts": _NOW - 25,
        "hits": 90,
        "duration_seconds": 5,
    }
    result = await _run_check(state, stamp_age_s=STATE_DECAY_S + 5)
    assert result is True


@pytest.mark.asyncio
async def test_decay_calls_publish_needs_id() -> None:
    """When decay fires, _publish_needs_id is invoked (via bcast.publish)."""
    state = _make_state()
    state.last_vinyl_confidence_set_at = _NOW - (STATE_DECAY_S + 5)
    orch = _make_orchestrator(state)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        fired = await orch._check_state_decay("vinyl", -40.0)
    assert fired is True
    orch.bcast.publish.assert_called_once()
    published = orch.bcast.publish.call_args[0][0]
    assert published.get("state") == "NEEDS_ID"
    assert published.get("title") is None


# ---------------------------------------------------------------------------
# Regression: predicted-advance must refresh the confidence stamp so a
# fresh predicted publish isn't killed by state-decay ~15s later.
# See docs/features/state-decay-kills-predicted-advance/.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predicted_advance_refreshes_confidence_stamp() -> None:
    """When _try_advance_prediction successfully publishes a predicted track,
    state.last_vinyl_confidence_set_at must be refreshed to "now" so the
    next state-decay check measures freshness from the predicted publish,
    not from the prior Shazam confirmation."""
    state = _make_state()
    # Stamp is 30s old — would NOT fire state-decay alone, but is the kind
    # of stamp that exists right when predicted-advance fires.
    state.last_vinyl_confidence_set_at = _NOW - 30.0
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A1",
        "side": "A",
        "title": "Track 1",
    }
    state.predicted_position = None
    orch = _make_orchestrator(state)
    broadcaster = AsyncMock()
    broadcaster.publish = AsyncMock()

    # Stub out the catalog + payload machinery so we exercise only the
    # stamp-refresh behaviour.
    advanced_track = {
        "release_id": 12345,
        "side": "A",
        "track_position": "A2",
        "title": "Track 2",
        "duration_seconds": 200,
    }
    orch._load_locked_tracks = MagicMock(return_value=[advanced_track])
    orch._resolve_advanced_track = MagicMock(return_value=advanced_track)
    orch._anchor_and_publish = lambda payload: payload

    with patch(
        "nowplaying.orchestrator._prediction_advance._build_predicted_payload",
        return_value={
            "title": "Track 2",
            "track_position": "A2",
            "side": "A",
            "release_id": 12345,
            "match_method": "predicted",
            "predicted": True,
        },
    ), patch("asyncio.get_running_loop") as mock_loop, patch(
        "nowplaying.history.record_play", new_callable=AsyncMock,
    ):
        mock_loop.return_value.time.return_value = _NOW
        published = await orch._try_advance_prediction(
            state, "vinyl", broadcaster,
        )

    assert published is True
    broadcaster.publish.assert_awaited_once()
    # The fix: stamp must be refreshed to current monotonic time.
    assert state.last_vinyl_confidence_set_at == _NOW


@pytest.mark.asyncio
async def test_state_decay_holds_after_predicted_advance() -> None:
    """End-to-end: a state-decay check immediately after a predicted-advance
    should NOT fire, even when the prior Shazam stamp was approaching the
    decay threshold."""
    state = _make_state()
    # Stamp is 40s old (still under STATE_DECAY_S=45s, but barely).
    # Without the fix, the next heartbeat after predicted-advance (15s later)
    # would put age at 55s and state-decay would fire — killing the just-
    # published predicted track. With the fix, predicted-advance refreshes
    # the stamp to NOW, so the next heartbeat sees age=15s and decay holds.
    state.last_vinyl_confidence_set_at = _NOW - 40.0
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A1",
        "side": "A",
        "title": "Track 1",
    }
    state.predicted_position = None
    orch = _make_orchestrator(state)
    broadcaster = AsyncMock()
    broadcaster.publish = AsyncMock()

    advanced_track = {
        "release_id": 12345,
        "side": "A",
        "track_position": "A2",
        "title": "Track 2",
        "duration_seconds": 200,
    }
    orch._load_locked_tracks = MagicMock(return_value=[advanced_track])
    orch._resolve_advanced_track = MagicMock(return_value=advanced_track)
    orch._anchor_and_publish = lambda payload: payload

    with patch(
        "nowplaying.orchestrator._prediction_advance._build_predicted_payload",
        return_value={
            "title": "Track 2",
            "track_position": "A2",
            "side": "A",
            "release_id": 12345,
            "match_method": "predicted",
            "predicted": True,
        },
    ), patch("asyncio.get_running_loop") as mock_loop, patch(
        "nowplaying.history.record_play", new_callable=AsyncMock,
    ):
        # T = NOW: predicted-advance fires
        mock_loop.return_value.time.return_value = _NOW
        await orch._try_advance_prediction(state, "vinyl", broadcaster)
        # T = NOW + 15s: next heartbeat, check state-decay
        mock_loop.return_value.time.return_value = _NOW + 15.0
        fired = await orch._check_state_decay("vinyl", -20.0)

    assert fired is False, (
        "state-decay fired 15s after predicted-advance — the predicted "
        "publish was prematurely killed (regression)"
    )


@pytest.mark.asyncio
async def test_predicted_advance_attaches_guess_to_payload() -> None:
    """When _try_advance_prediction publishes, the broadcast payload
    must include a guess object with the predicted title + position so
    the kiosk's deriveIdentifyState flips to 'awaiting-confirm' and
    surfaces the inline confirm card."""
    state = _make_state()
    state.last_vinyl_confidence_set_at = _NOW - 10.0
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A1",
        "side": "A",
        "title": "Track 1",
    }
    state.predicted_position = None
    orch = _make_orchestrator(state)
    broadcaster = AsyncMock()
    broadcaster.publish = AsyncMock()

    advanced_track = {
        "release_id": 12345,
        "side": "A",
        "track_position": "A2",
        "title": "Track 2",
        "duration_seconds": 200,
    }
    orch._load_locked_tracks = MagicMock(return_value=[advanced_track])
    orch._resolve_advanced_track = MagicMock(return_value=advanced_track)
    orch._anchor_and_publish = lambda payload: payload

    captured_payload: dict = {}

    async def capture(payload):
        captured_payload.update(payload)

    broadcaster.publish.side_effect = capture

    with patch(
        "nowplaying.orchestrator._prediction_advance._build_predicted_payload",
        return_value={
            "title": "Track 2",
            "track_position": "A2",
            "side": "A",
            "release_id": 12345,
            "match_method": "predicted",
            "predicted": True,
        },
    ), patch("asyncio.get_running_loop") as mock_loop, patch(
        "nowplaying.history.record_play", new_callable=AsyncMock,
    ):
        mock_loop.return_value.time.return_value = _NOW
        ok = await orch._try_advance_prediction(state, "vinyl", broadcaster)

    assert ok is True
    guess = captured_payload.get("guess")
    assert guess is not None, "predicted-advance must attach guess to payload"
    assert guess["title"] == "Track 2"
    assert guess["position"] == "A2"
    assert guess["confidence"] == "medium"
    assert guess["source"] == "heuristic"


@pytest.mark.asyncio
async def test_republish_current_prediction_attaches_guess() -> None:
    """The re-publish path (when streak > 1) must also attach guess so
    the inline card stays alive on the next heartbeat."""
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A2",
        "side": "A",
        "title": "Track 2",
    }
    state.predicted_position = {
        "release_id": 12345,
        "side": "A",
        "track_position": "A2",
        "index_in_side": 1,
    }
    orch = _make_orchestrator(state)
    broadcaster = AsyncMock()
    broadcaster.publish = AsyncMock()

    captured_payload: dict = {}

    async def capture(payload):
        captured_payload.update(payload)

    broadcaster.publish.side_effect = capture

    orch._anchor_and_publish = lambda payload: payload

    with patch(
        "nowplaying.orchestrator._prediction_advance._build_predicted_payload",
        return_value={
            "title": "Track 2",
            "track_position": "A2",
            "side": "A",
            "release_id": 12345,
            "match_method": "predicted",
            "predicted": True,
        },
    ), patch("nowplaying.history.record_play", new_callable=AsyncMock):
        ok = await orch._republish_current_prediction(state, "vinyl", broadcaster)

    assert ok is True
    guess = captured_payload.get("guess")
    assert guess is not None, "re-publish must attach guess"
    assert guess["title"] == "Track 2"
    assert guess["position"] == "A2"
    assert guess["confidence"] == "medium"
    assert guess["source"] == "heuristic"


# ---------------------------------------------------------------------------
# State-decay with pending_guess — route through predicted-advance, not NEEDS_ID
# See docs/features/llm-guess-renders-as-predicted/
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_decay_with_guess_calls_try_advance_not_publish_needs_id() -> None:
    """When state.pending_guess is set, the decay path must publish a
    predicted-advance payload (kiosk renders BEST GUESS card) instead of
    NEEDS_ID (bare identify screen).

    The routing now lives INSIDE _publish_needs_id (so both the
    state-decay path AND the streak path benefit), so this test
    verifies the effect: _try_advance_prediction is called with the
    guess's position, _publish_needs_id's NEEDS_ID emission is skipped.

    Regression for llm-guess-renders-as-predicted.
    """
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A3",
        "side": "A",
        "title": "Waves",
    }
    state.pending_guess = {
        "position": "A4",
        "title": "Light It",
        "confidence": "medium",
        "source": "llm",
        "alt": {"position": "A5", "title": "The New"},
    }
    orch = _make_orchestrator(state)
    orch._try_advance_prediction = AsyncMock(return_value=True)

    state.last_vinyl_confidence_set_at = _NOW - (STATE_DECAY_S + 5)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        result = await orch._check_state_decay("vinyl", -20.0)

    assert result is True
    orch._try_advance_prediction.assert_awaited_once()
    call_kwargs = orch._try_advance_prediction.call_args
    assert call_kwargs.kwargs.get("target_track_position") == "A4", (
        "must target the guess's position, not the heuristic source position"
    )
    # track_started_at_override must be set (back-dated) so the kiosk's
    # elapsed clock matches when the guess fires after the streak window.
    assert call_kwargs.kwargs.get("track_started_at_override") is not None
    # NEEDS_ID payload must NOT have been broadcast — broadcaster.publish
    # is the kiosk-facing emission point; the routing short-circuits
    # before _publish_needs_id's payload construction.
    orch.bcast.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_decay_without_guess_still_publishes_needs_id() -> None:
    """Regression guard: when no guess is pending, decay still publishes
    NEEDS_ID exactly as before."""
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A3",
        "side": "A",
        "title": "Waves",
    }
    state.pending_guess = None
    orch = _make_orchestrator(state)
    orch._publish_needs_id = AsyncMock()
    orch._try_advance_prediction = AsyncMock(return_value=True)

    state.last_vinyl_confidence_set_at = _NOW - (STATE_DECAY_S + 5)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        result = await orch._check_state_decay("vinyl", -20.0)

    assert result is True
    orch._publish_needs_id.assert_awaited_once()
    orch._try_advance_prediction.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_decay_guess_falls_back_to_needs_id_when_advance_fails() -> None:
    """If pending_guess is set but its position doesn't resolve on the
    locked side (F6 lookup returns None → _try_advance_prediction returns
    False), we must fall through to NEEDS_ID — never leave the kiosk in
    a stale state with no publish."""
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A3",
        "side": "A",
        "title": "Waves",
    }
    state.pending_guess = {
        "position": "Z99",  # Not on locked side's tracklist.
        "title": "Phantom",
        "confidence": "low",
        "source": "llm",
    }
    orch = _make_orchestrator(state)
    # _try_advance_prediction returns False (target position didn't resolve)
    orch._try_advance_prediction = AsyncMock(return_value=False)

    state.last_vinyl_confidence_set_at = _NOW - (STATE_DECAY_S + 5)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        result = await orch._check_state_decay("vinyl", -20.0)

    assert result is True
    # Routing attempted but failed.
    orch._try_advance_prediction.assert_awaited_once()
    # Broadcaster receives a NEEDS_ID-shaped payload as fallback (the
    # _publish_needs_id internal routing falls through when the routing
    # short-circuit returns False).
    orch.bcast.publish.assert_awaited()
    published = orch.bcast.publish.call_args.args[0]
    assert published.get("state") == "NEEDS_ID"


@pytest.mark.asyncio
async def test_state_decay_with_empty_position_guess_publishes_needs_id() -> None:
    """A pending_guess without a usable position is treated as no guess
    (defensive guard against malformed verdicts)."""
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A3",
        "side": "A",
        "title": "Waves",
    }
    state.pending_guess = {"position": "", "title": "", "confidence": "low"}
    orch = _make_orchestrator(state)
    orch._publish_needs_id = AsyncMock()
    orch._try_advance_prediction = AsyncMock(return_value=True)

    state.last_vinyl_confidence_set_at = _NOW - (STATE_DECAY_S + 5)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = _NOW
        await orch._check_state_decay("vinyl", -20.0)

    orch._try_advance_prediction.assert_not_awaited()
    orch._publish_needs_id.assert_awaited_once()


# ---------------------------------------------------------------------------
# Streak path also routes through guess (the second-half of the
# llm-guess-renders-as-predicted fix). See discussion in the 2026-05-22
# Dark Side of the Moon Time → NEEDS_ID transient incident.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_needs_id_routes_through_guess_when_pending_set() -> None:
    """The streak path (`_handle_unmatched_music_level` → `_seed_prediction_from_last_vinyl`
    → STAY → fall-through to `_publish_needs_id`) must ALSO route through
    predicted-advance when `state.pending_guess` is set.

    Before this fix the streak path emitted a bare NEEDS_ID payload even
    when track-guess had populated `state.pending_guess`, causing the
    kiosk to flash the bare identify screen instead of the BEST GUESS card.
    """
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A2",
        "side": "A",
        "title": "On The Run",
    }
    state.pending_guess = {
        "position": "A3",
        "title": "Time",
        "confidence": "high",
        "source": "llm",
    }
    orch = _make_orchestrator(state)
    orch._try_advance_prediction = AsyncMock(return_value=True)

    # Call _publish_needs_id directly (the streak path's end-of-line)
    await orch._publish_needs_id("vinyl", -20.0)

    # The internal routing fired — predicted-advance was attempted with
    # the guess's position; the NEEDS_ID payload was never broadcast.
    orch._try_advance_prediction.assert_awaited_once()
    call_kwargs = orch._try_advance_prediction.call_args
    assert call_kwargs.kwargs.get("target_track_position") == "A3"
    orch.bcast.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_needs_id_emits_needs_id_when_no_guess() -> None:
    """Regression: when no guess is pending, _publish_needs_id emits the
    bare NEEDS_ID payload as before."""
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A2",
        "side": "A",
        "title": "On The Run",
    }
    state.pending_guess = None
    orch = _make_orchestrator(state)
    orch._try_advance_prediction = AsyncMock(return_value=True)

    await orch._publish_needs_id("vinyl", -20.0)

    # Routing short-circuit didn't fire (no guess); NEEDS_ID published.
    orch._try_advance_prediction.assert_not_awaited()
    orch.bcast.publish.assert_awaited()
    published = orch.bcast.publish.call_args.args[0]
    assert published.get("state") == "NEEDS_ID"


@pytest.mark.asyncio
async def test_publish_needs_id_falls_through_when_guess_target_unresolved() -> None:
    """If the guess's position doesn't resolve on the locked side,
    _publish_needs_id must still emit the NEEDS_ID payload (never leave
    the kiosk silent)."""
    state = _make_state()
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A2",
        "side": "A",
        "title": "On The Run",
    }
    state.pending_guess = {
        "position": "Z99",  # Not in any tracklist
        "title": "Phantom",
        "confidence": "low",
        "source": "llm",
    }
    orch = _make_orchestrator(state)
    orch._try_advance_prediction = AsyncMock(return_value=False)

    await orch._publish_needs_id("vinyl", -20.0)

    # Routing attempted; advance returned False; NEEDS_ID published.
    orch._try_advance_prediction.assert_awaited_once()
    orch.bcast.publish.assert_awaited()
    published = orch.bcast.publish.call_args.args[0]
    assert published.get("state") == "NEEDS_ID"
