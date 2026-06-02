"""Pure helpers + constants for the streaming/AirPlay/vinyl idle paths."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nowplaying.vinyl.levels import MUSIC_DB

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State


# Consecutive unmatched recognitions after a confirmed track that trigger the
# idle timer (surface-noise-while-needle-down fallback).
MAX_UNMATCHED_STREAK = 4
# Music-level unmatched heartbeats that must accumulate before publishing
# NEEDS_ID. Two consecutive misses = ~30s of confidently no-ID before
# prompting the user to identify manually.
NEEDS_ID_STREAK = 2
# MUSIC_DB (imported from vinyl/levels.py — the single source of truth for the
# capture chain's level thresholds) distinguishes "real music we should try to
# ID" from "ambient/noise we shouldn't bother with." Used to gate shazam-only
# (no-Discogs) publishes, NEEDS_ID transitions, and music-level idle retraction
# at the top of on_heartbeat. It is the upper hysteresis bound the capture gate
# uses for its silent→audible flip, so "capture says audible" ⟺ "orchestrator
# says music."

# Capture heartbeat cadence in seconds. Must match capture_proto.py's
# `--heartbeat-s` default. Used by the streak-seeded prediction path to
# back-date `track_started_at` proportional to how long the song has
# likely been playing by the time the unmatched streak crosses the
# NEEDS_ID threshold.
HEARTBEAT_INTERVAL_S = 15

# Tolerance window (seconds) before end-of-track within which N-misses alone
# may seed a predicted-advance. If elapsed < duration - this value, a coverage
# gap is the most likely explanation for the misses — not a real track change.
# Rationale: Pitiful is 286s; refs cluster at 8–194s. At t=75s (26% through)
# refs run out and fingerprint starts missing. Without this guard the
# predicted-advance fires at t≈75s, flipping the kiosk to the wrong track.
# 30s gives one full heartbeat of slop before the track actually ends.
# See docs/features/predicted-advance-duration-guard/.
PREDICTED_ADVANCE_TOLERANCE_S = 30

# Vinyl silence-driven idle (existing 45s path, now a named
# constant for parity with the streaming threshold).
VINYL_IDLE_DELAY_S = 120
# Maximum age (seconds) of the last confident recognition before the
# orchestrator decays state.last_vinyl to None and transitions to
# NEEDS_ID. 3 heartbeats at 15s cadence — empirically: a real track
# keeps producing recognition hits that refresh the stamp; audio
# silence or genuinely unrecognizable audio does not.
# See docs/features/state-decay-when-stale/.
STATE_DECAY_S = 45
# Streaming / AirPlay pause-driven idle. 10 minutes — long enough
# that a quick pause-to-take-a-call doesn't bounce to idle, short
# enough that "left it paused overnight" doesn't greet the user
# with stale art at breakfast.
STREAMING_IDLE_DELAY_S = 10 * 60


# Time between actual track-start and when we publish — used to back-date
# `track_started_at` so client-side elapsed clocks (lyrics scroll, side
# timer) line up with the audio.
#   - shazam: capture-buffer (~12s) + Shazam round-trip
#   - sonos-didl / sonos-polled: UPnP event delivery + DIDL polling latency
RECOGNITION_LEAD_S = {
    "shazam": 12,
    # Predictions fire on the audible edge (audible-driven advance)
    # or just after the second unmatched heartbeat (streak-driven
    # seed). The audible-edge case wants the same ~2s back-date as
    # Sonos events; the streak-seeded case wants a larger back-date
    # — computed inline at publish time using NEEDS_ID_STREAK *
    # heartbeat_s — so this constant only covers the audible-edge
    # case here.
    "predicted": 2,
    # Sonos UPnP events arrive just slightly before the audible audio
    # (event-bus dispatch < AirPlay buffer drain), so a small back-date
    # keeps lyrics from sitting a line behind.
    "sonos-didl": 2,
    "sonos-polled": 2,
}

# Minimum alignment-vote count required to accept a fingerprint match on the
# anchored confirmation path (F3 — single-cohort scope, locked release ID).
# Calibrated from 2026-05-18 live session: correct Pitiful match = 136 hits;
# false Leo positives = 15–51 hits. 60 sits well above the false-positive
# ceiling and well below the true-positive floor, giving a 2× safety margin
# on each side. Tune upward if future sessions surface false positives above
# this value, or downward if true positives fall below it on sparser DBs.
MIN_FINGERPRINT_HITS_ANCHORED = 60

# Minimum alignment-vote count required to accept a fingerprint match on the
# blind discovery path (F4 — no album lock, scans all refs).  Blind scans
# have lower per-heartbeat hit counts because matches are diluted across the
# full reference DB and by partial-coverage refs (refs concentrated at the
# first ~3 minutes of a longer track).  Live evidence 2026-05-18: real Pitiful
# audio scored 15, 39, 54, 115 hits across heartbeats; the 54-hit heartbeat
# was a legitimate match rejected by the old unified threshold of 60.
# The absolute floor is intentionally thin — its job is to reject total noise
# (incoherent multi-ref scatter), not to substitute for the top-2 margin gate.
# The 2× margin requirement is the primary false-positive filter; this floor
# only rules out sub-noise hits where no single ref dominates.
MIN_FINGERPRINT_HITS_BLIND = 30

# Multiplier applied to MIN_FINGERPRINT_HITS_ANCHORED to determine the
# Strong-confidence threshold at which a blind match sets a fingerprint anchor
# (blocking predicted-advance).  At ANCHORED=60, multiplier=0.5 resolves to 30,
# matching MIN_FINGERPRINT_HITS_BLIND so any match strong enough to publish via
# the blind path also sets the anchor.  The prior 1.5x (anchor threshold 90)
# left a 30-89 zone where matches published but did not anchor — predicted-
# advance could then fire on the next miss-streak and flip to a wrong track.
# Live evidence 2026-05-18 16:56 and 2026-05-19 morning sessions: publish-
# worthy = anchor-worthy.  See docs/features/anchor-multiplier-tune/.
STRONG_FINGERPRINT_ANCHOR_MULTIPLIER = 0.5


def _is_music_level(level_db: float) -> bool:
    return level_db >= MUSIC_DB


def _should_arm_streaming_idle(
    payload_source: str | None,
    payload_state: str | None,
    idled_source: str | None,
    idle_task_alive: bool,  # skylos: ignore SKY-L029 — bool position is documented; tests use positional, main.py uses kwargs. Test signature is the contract.
) -> bool:
    """Decide whether on_sonos_event should arm the streaming/AirPlay
    pause-driven idle timer for this incoming event. Pure: no I/O,
    no state mutation. Caller passes `idle_task_alive` =
    `state.idle_task is not None and not state.idle_task.done()`.

    Arming conditions (all must hold):
      - Source is streaming or airplay.
      - Transport state is PAUSED_PLAYBACK. STOPPED is intentionally
        NOT armed because the kiosk's `showIdle` already idles
        immediately on STOPPED (`state === 'STOPPED'`) — arming a
        10-minute timer to publish another STOPPED would be a no-op
        for the visual layer and just burn an asyncio task.
      - No sticky idle already engaged (`idled_source is None`).
      - No idle task currently running (avoid stacking on Sonos's
        ~15s repoll cadence).
    """
    if payload_source not in ("streaming", "airplay"):
        return False
    if payload_state != "PAUSED_PLAYBACK":
        return False
    if idled_source is not None:
        return False
    if idle_task_alive:
        return False
    return True


def _should_cancel_streaming_idle_on_resume(
    payload_source: str | None,
    payload_state: str | None,
    idle_task_alive: bool,  # skylos: ignore SKY-L029 — bool position is documented; tests use positional, main.py uses kwargs. Test signature is the contract.
) -> bool:
    """Decide whether on_sonos_event should cancel a pending
    streaming/AirPlay idle timer because the user just hit resume.
    Pure helper. Fires on the paused→playing edge before the publish
    path runs, so the about-to-fire idle task can't publish a stale
    STOPPED on top of the live PLAYING event.
    """
    if payload_source not in ("streaming", "airplay"):
        return False
    if payload_state != "PLAYING":
        return False
    return idle_task_alive


def _evaluate_sticky_idle(
    idled_source: str | None,
    idled_title: str | None,
    payload_source: str | None,
    payload_state: str | None,
    payload_title: str | None,
) -> str:
    """Decide what to do with an incoming Sonos event while
    sticky-idle is engaged. Pure: no I/O, no state mutation.

    Returns:
      "no_op"         — sticky-idle is not engaged; treat event normally.
      "short_circuit" — same source/state/title as when we idled; drop event.
      "clear"         — genuine change; caller clears idled_source/title
                        then proceeds with the event.
    """
    if idled_source is None:
        return "no_op"
    same_source = payload_source == idled_source
    still_paused_or_stopped = payload_state in (
        "PAUSED_PLAYBACK", "STOPPED", "TRANSITIONING",
    )
    title_unchanged = idled_title == payload_title
    if same_source and still_paused_or_stopped and title_unchanged:
        return "short_circuit"
    return "clear"


def _should_pause_capture(state: "State") -> bool:
    """The orchestrator's view of whether capture should be emitting.

    Pause when neither vinyl nor (airplay-without-Sonos-metadata) is active:
    - vinyl: always run capture
    - airplay with Sonos metadata: pause (Sonos has the answer; UFO202 adds nothing)
    - airplay without Sonos metadata: run capture (system-audio AirPlay case)
    - streaming / TV / radio / unknown: pause
    """
    src = state.sonos_source
    if src == "vinyl":
        return False
    if src == "airplay" and not state.sonos_has_metadata:
        return False
    return True
