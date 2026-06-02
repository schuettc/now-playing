"""Pure decision helpers for the user-track-pin lifecycle."""
from __future__ import annotations

import calendar
import time


PIN_DIFFERENT_TRACK_RELEASE_STREAK = 3
# Pin uses no grace window: the pin's computed TTL is already conservative
# (PIN_SAFETY_MARGIN_S=30 was subtracted at pin time on the known-elapsed
# path; the fresh-start path uses the full track duration). Adding a buffer
# at the expiry check kept the pin "active" for up to 15s past its real
# expiry, during which promotion captured fingerprints under the now-wrong
# label — minor cohort poisoning at every track transition.
# See docs/features/pin-stays-active-past-ttl/.
PIN_TTL_BUFFER_S = 0
# Anchor keeps the historical 15s grace. Anchors are continuously refreshed
# on every blind fingerprint hit, so the buffer only matters in the rare
# window where matches stop arriving exactly at/near the track boundary —
# the buffer prevents flapping when the next track's first hit lands just
# after the previous track's nominal end.
ANCHOR_TTL_BUFFER_S = 15
# Subtracted from remaining track time when we *know* the track started
# some time ago (known-elapsed path). Guards against clock skew between
# track_started_at and the user's tap.
PIN_SAFETY_MARGIN_S = 30
# Floor so a pin set near the end of a track still survives at least this long.
MIN_PIN_TTL_S = 30
# Scaling-lock model. A manual lock is authoritative: its hold is derived from
# an estimate of where in the track the user locked (reliable cues only —
# fingerprint anchor / fresh-side audible-edge / Shazam first-miss), defaulting
# to a duration-capped assumed position when no cue exists (users typically
# lock a song a bit after it starts, but never "almost over" — so the assumption
# is min(ASSUMED_LOCK_POSITION_S, duration/3), see _apply_user_track_pin).
# Drift-prone dead-reckoning backdates never shorten a manual lock. As the
# lock's expected track-end approaches, its confidence decays over a window
# rather than ending on a hard cliff — advance opens up gradually and a
# different-track recognition overrides at any point. The decay window never
# consumes more than half the hold, so even a short or floored lock keeps a
# confident hard-hold first (see _effective_decay_window_s).
# See docs/features/advance-on-shazam-quiet-records/.
ASSUMED_LOCK_POSITION_S = 45
LOCK_DECAY_WINDOW_S = 45


def _effective_decay_window_s(duration: float) -> float:
    """The decay window for a hold of ``duration`` seconds: ``LOCK_DECAY_WINDOW_S``,
    but never more than half the hold. Without this cap a short or floored hold
    (duration <= LOCK_DECAY_WINDOW_S) would be born entirely inside its decay
    window — providing zero confident hard-hold, so a manual lock on a short
    track wouldn't actually hold. See docs/features/advance-on-shazam-quiet-records/."""
    return min(float(LOCK_DECAY_WINDOW_S), duration / 2.0)


def _pin_in_decay(pin: dict, now_mono: float) -> bool:
    """True when the pin is within the final (bounded) decay window of its hold —
    the soft zone where confidence ramps down and predicted-advance is allowed
    (but the track is still shown). False before the window (hard hold) and
    after expiry. Always False when the pin has no duration."""
    duration = pin.get("duration_seconds")
    if duration is None:
        return False
    elapsed = now_mono - pin["monotonic_ts"]
    decay_start = duration - _effective_decay_window_s(duration)
    return decay_start <= elapsed <= (duration + PIN_TTL_BUFFER_S)


def _pin_confidence(pin: dict, now_mono: float) -> str:
    """Confidence the lock places on its current track: 'high' during the hard
    hold, ramping 'medium' then 'low' across the (bounded) decay window. 'high'
    when the pin has no duration (open-ended hold)."""
    duration = pin.get("duration_seconds")
    if duration is None:
        return "high"
    elapsed = now_mono - pin["monotonic_ts"]
    decay = _effective_decay_window_s(duration)
    decay_start = duration - decay
    if elapsed < decay_start:
        return "high"
    if decay <= 0:
        return "low"
    frac = (elapsed - decay_start) / decay  # 0 → 1 across the window
    return "medium" if frac < 0.5 else "low"


def _remaining_and_confidence(
    monotonic_ts: float, duration_seconds: float | None, now_mono: float,
) -> tuple[float | None, str]:
    """Shared track-remaining + confidence primitive (epic
    consolidate-guess-confidence-lifetime, child C1).

    Operates on anything modeled as a *hold* — a user pin, or a dead-reckoned
    guess whose ``duration_seconds`` is the estimated remaining track time at
    guess time. Returns ``(seconds_remaining, confidence)`` where confidence is
    'high' through the confident hold, ramping 'medium' then 'low' across the
    bounded decay window as the hold runs out. ``duration_seconds=None`` →
    ``(None, 'high')`` (open-ended hold). ``seconds_remaining`` may go negative
    once the hold is past its end; callers clamp/treat ≤0 as expired.

    This is the single decay model for both locks and guesses — the lock path
    (``_pin_in_decay`` / ``_pin_confidence``) and the guess payload both derive
    their confidence + lifetime from it.
    """
    if duration_seconds is None:
        return (None, "high")
    remaining = float(duration_seconds) - (now_mono - monotonic_ts)
    obj = {"monotonic_ts": monotonic_ts, "duration_seconds": duration_seconds}
    return (remaining, _pin_confidence(obj, now_mono))


def compute_pin_duration(
    duration_seconds: int | None,
    track_started_at_iso: str | None,
) -> int | None:
    """Compute the effective TTL duration for a new user-track-pin.

    Two paths:

    1. ``track_started_at_iso`` is a parseable ISO timestamp — the caller
       knows when the current track started. TTL is shrunk to the remaining
       time at click with a safety margin for clock skew::

           elapsed   = now − track_started_at
           remaining = duration − elapsed − PIN_SAFETY_MARGIN_S
           ttl       = max(MIN_PIN_TTL_S, remaining)

    2. ``track_started_at_iso`` is ``None`` (or unparseable) — fresh-start
       signal. The caller is telling us "this track just started, the user
       pinned at ~t=0." TTL is the full ``duration_seconds`` so the pin
       covers the entire track. No elapsed/safety subtraction applies
       because there is no measured elapsed time to be wrong about.

    Returns ``None`` when ``duration_seconds`` is unknown — the pin then
    never expires via TTL (cleared by audible-boundary or streak logic only).
    """
    if duration_seconds is None:
        return None

    if track_started_at_iso is None:
        return int(duration_seconds)

    try:
        # calendar.timegm interprets the struct_time as UTC, which matches
        # the "Z" suffix convention used throughout the codebase.
        started = calendar.timegm(time.strptime(track_started_at_iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError, OSError):
        # Unparseable timestamp — treat as fresh-start (no elapsed knowledge).
        return int(duration_seconds)

    elapsed_at_click = max(0.0, time.time() - started)
    remaining = int(duration_seconds) - elapsed_at_click - PIN_SAFETY_MARGIN_S
    return max(MIN_PIN_TTL_S, int(remaining))


def _pin_ttl_expired(pin: dict, now_mono: float) -> bool:
    """True when the pin has a duration and we're past it + buffer."""
    duration = pin.get("duration_seconds")
    if duration is None:
        return False
    elapsed = now_mono - pin["monotonic_ts"]
    return elapsed > duration + PIN_TTL_BUFFER_S


def _fingerprint_anchor_ttl_expired(anchor: dict, now_mono: float) -> bool:
    """True when the fingerprint anchor has a duration and we're past it + buffer.

    Same structure as ``_pin_ttl_expired`` but uses ``ANCHOR_TTL_BUFFER_S``
    instead of zero — anchors are refreshed on every blind fingerprint hit
    and the small grace window prevents flapping at track boundaries.
    When ``duration_seconds`` is ``None`` (track not in Discogs catalog with
    duration data), the anchor never expires via TTL — it is cleared only
    on album-lock change or idle.
    """
    duration = anchor.get("duration_seconds")
    if duration is None:
        return False
    elapsed = now_mono - anchor["monotonic_ts"]
    return elapsed > duration + ANCHOR_TTL_BUFFER_S


def _evaluate_user_pin(
    pin: dict | None,
    streak: int,
    rid: int | None,
    position: str | None,
    now_mono: float,
) -> tuple[str, int, str]:
    """Pure decision for the user-track-pin lifecycle.

    Returns ``(action, new_streak, reason)`` where action is one of:
      - ``"pass"`` — no pin / not our concern; caller proceeds normally
      - ``"honor"`` — pin still valid; caller patches payload identity
      - ``"clear"`` — release the pin; caller falls through normally

    ``reason`` tags the decision for logging.
    """
    if pin is None:
        return ("pass", 0, "no_pin")
    if _pin_ttl_expired(pin, now_mono):
        return ("clear", 0, "ttl")
    if rid is not None and rid != pin["release_id"]:
        return ("clear", 0, "different_release")
    if rid is None:
        return ("honor", streak, "shazam_only")
    pinned_pos = (pin["track_position"] or "").strip().upper()
    hit_pos = (position or "").strip().upper()
    if pinned_pos == hit_pos:
        return ("honor", 0, "same_position")
    new_streak = streak + 1
    # Confidence decay: the streak needed to override the lock with a
    # different same-release track shrinks as the lock nears its expected end.
    # During the confident hold a single cross-track hit can't flip the lock
    # (PIN_DIFFERENT_TRACK_RELEASE_STREAK corroborations); in the decay window
    # a different-track recognition takes over after fewer hits.
    threshold = {
        "high": PIN_DIFFERENT_TRACK_RELEASE_STREAK,
        "medium": 2,
        "low": 1,
    }[_pin_confidence(pin, now_mono)]
    if new_streak >= threshold:
        return ("clear", 0, "streak_exceeded")
    return ("honor", new_streak, "different_position")
