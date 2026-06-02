"""Unmatched-heartbeat routing — music-level and below-music paths.

Contains: _try_publish_guess_as_predicted, _handle_unmatched_heartbeat,
_decide_suppress_advance, _decide_anchored_advance, _decide_cold_start,
_decide_post_skip, _handle_unmatched_music_level,
_seed_prediction_from_last_vinyl, _publish_needs_id.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import recognize_proto

from nowplaying.orchestrator._first_miss import (
    clear_first_miss_after_match,
    mark_first_miss_after_match,
)
from nowplaying.orchestrator.advance import _compute_advance_elapsed_s
from nowplaying.orchestrator.pin import (
    _fingerprint_anchor_ttl_expired,
    _pin_in_decay,
    _pin_ttl_expired,
)
from nowplaying.orchestrator.streaming_idle import (
    HEARTBEAT_INTERVAL_S,
    MAX_UNMATCHED_STREAK,
    NEEDS_ID_STREAK,
    PREDICTED_ADVANCE_TOLERANCE_S,
    RECOGNITION_LEAD_S,
    VINYL_IDLE_DELAY_S,
    _is_music_level,
)

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


class _UnmatchedMixin:
    """Unmatched-heartbeat routing.  Mixed into PredictionMixin."""

    async def _try_publish_guess_as_predicted(
        self, state: "State", audio_source_label: str,
    ) -> bool:
        """If `state.pending_guess` is set with a usable position, publish a
        predicted-advance payload targeted at that position. Returns True on
        success, False when no guess exists or its position doesn't resolve
        on the locked side (caller should fall back to NEEDS_ID).

        Skips _maybe_consult_llm_for_advance — the LLM has already been
        consulted via judge_track_guess (which produced pending_guess).

        The LLM verdict in `state.pending_guess` overrides the heuristic
        guess that `_try_advance_prediction` sets on the payload, because
        `_attach_pending_guess` runs after and gives `state.pending_guess`
        precedence. The full LLM reasoning (alt, source='llm') is preserved.
        """
        guess = state.pending_guess
        if guess is None:
            return False
        target_pos = guess.get("position")
        if not target_pos:
            return False
        seed_back_s = (
            NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S
            + RECOGNITION_LEAD_S.get("predicted", 2)
        )
        seed_anchor = (
            datetime.now(timezone.utc) - timedelta(seconds=seed_back_s)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        return await self._try_advance_prediction(
            state, audio_source_label, self.bcast,
            track_started_at_override=seed_anchor,
            target_track_position=target_pos,
        )

    async def _handle_unmatched_heartbeat(
        self, audio_source_label: str, level_db: float,
    ) -> None:
        """Increment the unmatched-streak counter and route to either the
        music-level NEEDS_ID flow or the below-music idle escalation."""
        state = self.state
        if await self._check_state_decay(audio_source_label, level_db):
            return
        state.unmatched_streak += 1
        mark_first_miss_after_match(state)
        if _is_music_level(level_db):
            await self._handle_unmatched_music_level(audio_source_label, level_db)
            return
        # Below music level: escalate to idle after MAX_UNMATCHED_STREAK
        # heartbeats of sustained silence, regardless of whether a track
        # was previously recognized. Cold-start (Sonos on Line-In, no
        # needle ever) must also reach idle — without this, the kiosk
        # sits on the identifying screen indefinitely.
        if state.unmatched_streak < MAX_UNMATCHED_STREAK:
            return
        log.info(
            "unmatched streak hit %d — treating as silence; starting idle timer",
            state.unmatched_streak,
        )
        if state.idle_task is None or state.idle_task.done():
            state.idle_task = asyncio.create_task(
                self._idle_after_delay(VINYL_IDLE_DELAY_S, audio_source_label),
            )
        state.unmatched_streak = 0

    def _decide_suppress_advance(
        self, state: "State", now_mono: float,
    ) -> bool:
        """Return True when an active pin or fingerprint anchor suppresses
        predicted-advance and NEEDS_ID transitions.  Logs the reason.
        """
        pin = state.user_track_pin
        # A manual lock is authoritative but scaling: it hard-suppresses advance
        # only during its confident hold. Once it enters the final
        # LOCK_DECAY_WINDOW_S (approaching the expected track end) suppression
        # lifts so predicted-advance can fire as the track really ends — the
        # lock's confidence decays rather than ending on a cliff.
        if (
            pin is not None
            and not _pin_ttl_expired(pin, now_mono)
            and not _pin_in_decay(pin, now_mono)
        ):
            log.info(
                "predicted: suppressed advance (pin active "
                "release=%s pos=%s title=%r streak=%d)",
                pin.get("release_id"),
                pin.get("track_position"),
                (state.last_vinyl or {}).get("title"),
                state.unmatched_streak,
            )
            return True
        anchor = state.fingerprint_anchor
        if anchor is not None and not _fingerprint_anchor_ttl_expired(anchor, now_mono):
            log.info(
                "predicted: suppressed advance (fingerprint anchor "
                "release=%s pos=%s hits=%d streak=%d)",
                anchor.get("release_id"),
                anchor.get("track_position"),
                anchor.get("hits", 0),
                state.unmatched_streak,
            )
            return True
        return False

    async def _decide_anchored_advance(
        self, state: "State", audio_source_label: str,
    ) -> bool:
        """Re-publish the existing prediction when predicted_position is already
        set (audible-edge already advanced, or streak re-entry above NEEDS_ID).
        Returns True if published (caller should return), False otherwise.
        """
        if state.last_vinyl is None or state.predicted_position is None:
            return False
        log.info(
            "predicted: re-publishing current prediction "
            "(streak=%d, position=%s)",
            state.unmatched_streak,
            state.predicted_position.get("track_position"),
        )
        await self._republish_current_prediction(
            state, audio_source_label, self.bcast,
        )
        return True

    async def _decide_cold_start(
        self, state: "State", audio_source_label: str,
    ) -> bool | None:
        """Seed a fresh prediction at streak == NEEDS_ID_STREAK with a duration
        guard (Rule A).

        Returns True  — prediction seeded or LLM advanced (caller returns).
        Returns False — duration guard fired and LLM did not advance; fall
                        through to NEEDS_ID.
        Returns None  — streak != NEEDS_ID_STREAK or no last_vinyl; skip.
        """
        if state.unmatched_streak != NEEDS_ID_STREAK or state.last_vinyl is None:
            return None
        # Duration guard (Rule A): N-misses alone are NOT an affirmative
        # signal that the track changed. A coverage gap mid-track produces
        # exactly the same miss pattern. Only allow predicted-advance from
        # N-misses when elapsed >= duration - tolerance.
        # See docs/features/predicted-advance-duration-guard/.
        seed_back_s = NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S
        elapsed_s = _compute_advance_elapsed_s(
            state.track_started_at, seed_back_s,
        )
        duration_s = state.last_vinyl.get("duration_seconds")
        if duration_s is not None and elapsed_s < duration_s - PREDICTED_ADVANCE_TOLERANCE_S:
            log.info(
                "predicted: suppressed advance (N-misses only, mid-track "
                "coverage gap — elapsed=%.1fs duration=%ds tolerance=%ds "
                "streak=%d)",
                elapsed_s, duration_s, PREDICTED_ADVANCE_TOLERANCE_S,
                state.unmatched_streak,
            )
            # LLM override (llm-track-change-primary): consult Haiku on this
            # ambiguous coverage-gap heartbeat when ANTHROPIC_API_KEY is set.
            if self.llm.enabled:
                llm_advanced = await self._maybe_llm_override_rule_a(
                    state, audio_source_label, elapsed_s,
                )
                if llm_advanced:
                    return True
            return False
        if await self._seed_prediction_from_last_vinyl(audio_source_label):
            return True
        # End-of-side, no track_position to seed from — fall through.
        return False

    def _decide_post_skip(self, state: "State") -> bool:
        """Return True (and log) when we're already in NEEDS_ID and the streak
        is still climbing with no expired pin — caller should return without
        re-publishing.

        Returns False when the pin just expired at a high streak (caller should
        fall through to _publish_needs_id so the kiosk can update).
        """
        if state.unmatched_streak > NEEDS_ID_STREAK and state.user_track_pin is None:
            # Already transitioned to NEEDS_ID at streak == NEEDS_ID_STREAK.
            log.info(
                "music-level unmatched: streak=%d (still in NEEDS_ID)",
                state.unmatched_streak,
            )
            return True
        return False

    async def _handle_unmatched_music_level(
        self, audio_source_label: str, level_db: float,
    ) -> None:
        """Music-level unmatched heartbeat: wait for streak, re-publish a live
        prediction, seed a fresh prediction at streak == NEEDS_ID_STREAK, or
        transition the kiosk to NEEDS_ID.
        """
        state = self.state
        if state.unmatched_streak < NEEDS_ID_STREAK:
            log.info(
                "music-level unmatched: streak=%d/%d (waiting)",
                state.unmatched_streak, NEEDS_ID_STREAK,
            )
            return
        now_mono = asyncio.get_running_loop().time()
        # Active pin or fingerprint anchor suppresses advance and NEEDS_ID
        # transitions until the guard expires or a positive recognition fires.
        if self._decide_suppress_advance(state, now_mono):
            return
        # Tracklist-aware advancement: re-publish if prediction already set,
        # seed if streak just hit NEEDS_ID_STREAK, or fall to NEEDS_ID.
        if await self._decide_anchored_advance(state, audio_source_label):
            return
        cold = await self._decide_cold_start(state, audio_source_label)
        if cold is True:
            return
        if cold is False:
            # Duration guard or end-of-side — fall through to NEEDS_ID below.
            pass
        elif self._decide_post_skip(state):
            return
        # Transition: streak just hit NEEDS_ID_STREAK (or pin just expired at
        # a higher streak) — publish a listening/needs-id payload.
        await self._publish_needs_id(audio_source_label, level_db)

    async def _seed_prediction_from_last_vinyl(
        self, audio_source_label: str,
    ) -> bool:
        """Seed a fresh prediction at streak == NEEDS_ID_STREAK with a
        back-dated track_started_at so the kiosk's lyrics clock doesn't lag
        the actual audio. Returns True if seeded, False if end-of-side or
        nothing to seed from.

        F6 LLM hook (optional): when enabled, asks Haiku which track on
        the locked side the needle is on now. The LLM verdict overrides
        the heuristic "advance to next sequential track" pick.
        """
        state = self.state
        # The song has actually been playing for roughly NEEDS_ID_STREAK
        # heartbeats by now — pre-stamp track_started_at with that back-date.
        # Heartbeat interval default is 15s; if the orchestrator ever runs
        # capture with a different interval the math drifts, but the small
        # constant is fine because we'd rather under-shift than over-shift
        # past the actual song boundary.
        seed_back_s = (
            NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S
            + RECOGNITION_LEAD_S.get("predicted", 2)
        )
        seed_anchor = (
            datetime.now(timezone.utc) - timedelta(seconds=seed_back_s)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")

        # F6 LLM advance hook: pick the target track explicitly when enabled.
        target_pos = await self._maybe_consult_llm_for_advance(
            state, seed_back_s,
        )
        if target_pos == "STAY":
            # LLM signaled the needle is still on last_vinyl — bump the
            # streak back so the next heartbeat reaches NEEDS_ID_STREAK
            # again and we re-ask rather than dead-end at NEEDS_ID.
            state.unmatched_streak = max(NEEDS_ID_STREAK - 2, 0)
            return False

        return await self._try_advance_prediction(
            state, audio_source_label, self.bcast,
            track_started_at_override=seed_anchor,
            target_track_position=target_pos,
        )

    async def _publish_needs_id(
        self, audio_source_label: str, level_db: float,
    ) -> None:
        """Publish the NEEDS_ID transition payload so the kiosk's /identify
        page becomes the way to manually resolve the unidentified track.
        Drops any active idle timer, clears the user pin (it's been
        invalidated by the unmatched run).

        For airplay/streaming sources the Sonos listener is authoritative —
        a cascade miss is expected (speaker bleed) and must never overwrite
        valid Sonos metadata. Guard fires first so no state is mutated.

        If `state.pending_guess` is set, this method routes through
        ``_try_publish_guess_as_predicted`` instead of emitting NEEDS_ID.
        That keeps the kiosk on a track-renderable payload (the BEST
        GUESS card) rather than dropping to the bare identify screen.
        NEEDS_ID is reserved for the truly-no-guess case.

        Applied here (not at call sites) so BOTH the state-decay path
        (``_check_state_decay``) AND the streak path
        (``_handle_unmatched_music_level`` → ``_seed_prediction_from_last_vinyl``
        → STAY → fall-through to publish_needs_id) benefit. See
        docs/features/llm-guess-renders-as-predicted/.
        """
        state = self.state
        bcast = self.bcast
        # Cascade-derived NEEDS_ID must never overwrite Sonos-authoritative
        # metadata for streaming/AirPlay sources. Guard fires before any
        # mutation so pin/idle state is preserved.
        if state.sonos_source in ("airplay", "streaming"):
            log.info(
                "cascade publish suppressed: sonos_source=%s (needs_id)",
                state.sonos_source,
            )
            return
        # Route through predicted-advance when a pending LLM track-guess
        # exists — kiosk renders BEST GUESS card instead of bare needs-id.
        if await self._try_publish_guess_as_predicted(state, audio_source_label):
            return
        prev = state.last_vinyl
        log.info(
            "NEEDS_ID: level_db=%.1f prev=%s",
            level_db, (prev or {}).get("title"),
        )
        payload = {
            "ts": recognize_proto.now_iso(),
            "state": "NEEDS_ID",
            "source": audio_source_label,
            "title": None,
            "previous": {
                "release_id": prev.get("release_id"),
                "track_position": prev.get("track_position"),
                "title": prev.get("title"),
                "artist": prev.get("artist"),
                "art_url": prev.get("art_url"),
            } if prev is not None else None,
            "match_method": "unmatched",
        }
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        if state.user_track_pin is not None:
            log.info("pin released: reason=needs_id")
        state.user_track_pin = None
        state.pin_different_track_streak = 0
        state.fingerprint_anchor = None
        state.last_vinyl_confidence_set_at = None
        state.last_shazam_match_unix_ts = None
        state.last_pin_unix_ts = None
        clear_first_miss_after_match(state)
        # Route through _anchor_and_publish so any pending track-guess
        # (set on the Shazam-miss + fingerprint-miss path) is attached to
        # the NEEDS_ID payload. NEEDS_ID is the primary state where a
        # guess is valuable to the user. See docs/features/llm-track-guess/.
        await bcast.publish(self._anchor_and_publish(payload))
