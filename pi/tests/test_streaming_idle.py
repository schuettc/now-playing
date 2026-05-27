"""Tests for the streaming / AirPlay pause-driven idle path.

The bulk of the logic lives inside `main_async()` closures and is
exercised by the manual smoke documented in
docs/features/streaming-idle/shipped.md. The decision-by-decision
unit tests here cover the pure `_evaluate_sticky_idle` helper —
the most failure-prone part of the feature — and a few
State-mutation invariants.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.main import (  # noqa: E402
    STREAMING_IDLE_DELAY_S,
    VINYL_IDLE_DELAY_S,
    State,
    _evaluate_sticky_idle,
    _should_arm_streaming_idle,
    _should_cancel_streaming_idle_on_resume,
)


# ---- constants -------------------------------------------------------


def test_streaming_idle_delay_is_ten_minutes():
    assert STREAMING_IDLE_DELAY_S == 600


def test_vinyl_idle_delay_is_two_minutes():
    # Bumped from 45s to 120s so cold-start (no prior recognition,
    # Sonos on Line-In with no audio) takes long enough that an
    # in-progress needle drop is not interrupted. See
    # docs/features/idle-on-vinyl-silence/.
    assert VINYL_IDLE_DELAY_S == 120


# ---- State defaults --------------------------------------------------


def test_state_initializes_sticky_idle_fields_to_none():
    s = State()
    assert s.idled_source is None
    assert s.idled_title is None
    assert s.idle_task is None


# ---- _evaluate_sticky_idle: not-idled fast path ---------------------


def test_no_op_when_idle_not_engaged():
    """Most events: sticky-idle is not set, helper returns no_op."""
    assert _evaluate_sticky_idle(
        idled_source=None,
        idled_title=None,
        payload_source="streaming",
        payload_state="PLAYING",
        payload_title="Some Track",
    ) == "no_op"


# ---- _evaluate_sticky_idle: short_circuit cases ---------------------


def test_short_circuit_when_repoll_matches_idled_state():
    """The exact case the sticky-idle flag was designed to catch —
    Sonos's ~15s repoll re-announces the same paused track. Helper
    must say 'short_circuit' so on_sonos_event drops the event and
    the kiosk stays on the IdleScreen clock."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        payload_title="Road to Nowhere",
    ) == "short_circuit"


def test_short_circuit_when_event_is_stopped_after_idle():
    """STOPPED is one of the still-not-playing states; also short-circuits."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="STOPPED",
        payload_title="Road to Nowhere",
    ) == "short_circuit"


def test_short_circuit_when_event_is_transitioning_after_idle():
    """TRANSITIONING (the brief play↔pause in-between) must NOT trip
    the clear branch — it's a transient state, not a wake event."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="TRANSITIONING",
        payload_title="Road to Nowhere",
    ) == "short_circuit"


def test_short_circuit_works_for_airplay_too():
    """The gate is on source identity (not a hardcoded 'streaming')."""
    assert _evaluate_sticky_idle(
        idled_source="airplay",
        idled_title="Some Track",
        payload_source="airplay",
        payload_state="PAUSED_PLAYBACK",
        payload_title="Some Track",
    ) == "short_circuit"


# ---- _evaluate_sticky_idle: clear cases -----------------------------


def test_clear_when_state_transitions_to_playing():
    """User hit resume on their phone. PLAYING means wake the kiosk."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="PLAYING",
        payload_title="Road to Nowhere",
    ) == "clear"


def test_clear_when_user_scrubs_to_different_track_while_paused():
    """User advanced to a different track on their phone while paused.
    Title changed → wake the kiosk to show the new metadata (reviewer's
    broader-wake recommendation from round-2 plan review)."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        payload_title="Burning Down the House",
    ) == "clear"


def test_clear_when_source_changes():
    """Vinyl needle drops while a streaming session was idled-out.
    Source change → wake the kiosk."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="vinyl",
        payload_state="PLAYING",
        payload_title="Side A1",
    ) == "clear"


def test_clear_when_idled_title_was_none_and_new_title_present():
    """Idle from a queue-empty STOPPED (no title), then user picks
    a new track. Title goes from None → 'Some Track' → clear."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title=None,
        payload_source="streaming",
        payload_state="PLAYING",
        payload_title="Some Track",
    ) == "clear"


# ---- _evaluate_sticky_idle: combinatoric -----------------------------


def test_short_circuit_requires_all_three_matches():
    """Source mismatch alone is enough to clear."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="X",
        payload_source="airplay",  # different
        payload_state="PAUSED_PLAYBACK",
        payload_title="X",
    ) == "clear"


def test_title_unchanged_with_none_on_both_sides_is_short_circuit():
    """Two consecutive None-title repolls (e.g., during a transport
    transition) should still short-circuit if everything else matches."""
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title=None,
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        payload_title=None,
    ) == "short_circuit"


# ---- _should_arm_streaming_idle --------------------------------------


def test_arm_when_streaming_paused_with_no_idle_engaged():
    """The canonical case: Sonos PAUSED_PLAYBACK on streaming with
    nothing already running."""
    assert _should_arm_streaming_idle(
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        idled_source=None,
        idle_task_alive=False,
    ) is True


def test_arm_when_airplay_paused():
    """Same gate covers airplay, not just streaming."""
    assert _should_arm_streaming_idle(
        payload_source="airplay",
        payload_state="PAUSED_PLAYBACK",
        idled_source=None,
        idle_task_alive=False,
    ) is True


def test_no_arm_when_stopped():
    """STOPPED is intentionally not armed — the kiosk's showIdle
    already idles on STOPPED, so a 10min timer to publish another
    STOPPED would be a no-op for the visual layer. This decision
    differs from the original plan; see plan.md 'Phase 3'."""
    assert _should_arm_streaming_idle(
        payload_source="streaming",
        payload_state="STOPPED",
        idled_source=None,
        idle_task_alive=False,
    ) is False


def test_no_arm_when_playing():
    """Playing events never arm idle; they should cancel via the
    separate _should_cancel_streaming_idle_on_resume helper."""
    assert _should_arm_streaming_idle(
        payload_source="streaming",
        payload_state="PLAYING",
        idled_source=None,
        idle_task_alive=False,
    ) is False


def test_no_arm_when_source_not_streaming_or_airplay():
    """Vinyl uses the capture-silence path; TV/line-in have no idle path."""
    for src in ("vinyl", "tv", "line-in", "unknown", None):
        assert _should_arm_streaming_idle(
            payload_source=src,
            payload_state="PAUSED_PLAYBACK",
            idled_source=None,
            idle_task_alive=False,
        ) is False, f"unexpected arm for source={src!r}"


def test_no_arm_when_sticky_idle_already_engaged():
    """Once we've published STOPPED post-idle, repolls with
    PAUSED_PLAYBACK still arrive — but the sticky flag means
    arming again would just stack tasks."""
    assert _should_arm_streaming_idle(
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        idled_source="streaming",
        idle_task_alive=False,
    ) is False


def test_no_arm_when_idle_task_already_running():
    """Sonos repolls every 15s. If we already armed a 10min timer
    on the first PAUSED event, the next ~40 repolls must not
    stack additional tasks."""
    assert _should_arm_streaming_idle(
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        idled_source=None,
        idle_task_alive=True,
    ) is False


# ---- _should_cancel_streaming_idle_on_resume -------------------------


def test_cancel_on_streaming_play_with_task_alive():
    """User hit resume on their phone during the 10min window."""
    assert _should_cancel_streaming_idle_on_resume(
        payload_source="streaming",
        payload_state="PLAYING",
        idle_task_alive=True,
    ) is True


def test_cancel_on_airplay_play():
    assert _should_cancel_streaming_idle_on_resume(
        payload_source="airplay",
        payload_state="PLAYING",
        idle_task_alive=True,
    ) is True


def test_no_cancel_when_no_task_running():
    """Common case: PLAYING events with no pending idle. No-op."""
    assert _should_cancel_streaming_idle_on_resume(
        payload_source="streaming",
        payload_state="PLAYING",
        idle_task_alive=False,
    ) is False


def test_no_cancel_when_still_paused():
    """PAUSED→PAUSED repoll shouldn't cancel the timer we just armed."""
    assert _should_cancel_streaming_idle_on_resume(
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        idle_task_alive=True,
    ) is False


def test_no_cancel_for_vinyl():
    """Vinyl resume is handled by the capture-heartbeat retract path;
    this helper only owns the streaming/airplay resume edge."""
    assert _should_cancel_streaming_idle_on_resume(
        payload_source="vinyl",
        payload_state="PLAYING",
        idle_task_alive=True,
    ) is False


# ---- end-to-end decision combos --------------------------------------


def test_full_lifecycle_via_helpers():
    """Walks the full happy path entirely through the pure helpers
    to catch any cross-helper inconsistency.

    Events: PAUSED → (sticky=no_op, arm=True)
            repoll PAUSED → (sticky=short_circuit if idle fires first;
                              otherwise arm=False because task alive)
            idle fires → state.idled_source = "streaming"
            repoll PAUSED → sticky=short_circuit
            user resumes PLAYING → sticky=clear, cancel=True
    """
    # Initial pause arrives, no sticky, no task.
    assert _should_arm_streaming_idle(
        "streaming", "PAUSED_PLAYBACK", None, False,
    ) is True

    # Re-pause repoll while task is alive — should NOT re-arm.
    assert _should_arm_streaming_idle(
        "streaming", "PAUSED_PLAYBACK", None, True,
    ) is False

    # Sticky engaged; subsequent same-paused repoll short-circuits.
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="PAUSED_PLAYBACK",
        payload_title="Road to Nowhere",
    ) == "short_circuit"

    # User resumes. Sticky clears AND cancel fires.
    assert _evaluate_sticky_idle(
        idled_source="streaming",
        idled_title="Road to Nowhere",
        payload_source="streaming",
        payload_state="PLAYING",
        payload_title="Road to Nowhere",
    ) == "clear"
    # After clear, idle_task is no longer alive (helper got called),
    # so the cancel helper is exercised when alive=True (i.e., during
    # the same on_sonos_event invocation before cancel runs).
    assert _should_cancel_streaming_idle_on_resume(
        "streaming", "PLAYING", True,
    ) is True
