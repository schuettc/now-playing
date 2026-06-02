"""State-decay helpers — when to clear stale last_vinyl state.

Contains: _decay_pin_check, _check_state_decay.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nowplaying.orchestrator._first_miss import (
    clear_first_miss_after_match,
)
from nowplaying.orchestrator.pin import (
    _fingerprint_anchor_ttl_expired,
    _pin_ttl_expired,
)
from nowplaying.orchestrator.prediction import _elapsed_in_track_s
from nowplaying.orchestrator.streaming_idle import (
    NEEDS_ID_STREAK,
    STATE_DECAY_S,
)

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


def _track_lifetime_elapsed(state: "State", age: float) -> bool:
    """True when the current track's expected play time is up — the state-decay
    trigger. With a known duration + start, decay once we're past the track's
    end (track-remaining ≤ 0), so a predicted/guessed track lives as long as the
    track itself instead of a flat ~45s. Falls back to the flat STATE_DECAY_S
    confidence-stamp age when the duration is unknown.
    Epic consolidate-guess-confidence-lifetime / C3.
    """
    lv = state.last_vinyl or {}
    duration = lv.get("duration_seconds")
    started = lv.get("track_started_at")
    if duration is not None and started:
        return float(duration) - _elapsed_in_track_s(started) <= 0
    return age >= STATE_DECAY_S


class _DecayMixin:
    """State-decay helpers.  Mixed into PredictionMixin."""

    def _decay_pin_check(
        self, state: "State", now_mono: float, age: float,
    ) -> bool | None:
        """Check whether the active pin suppresses state-decay or should be
        cleared due to TTL expiry.

        Returns True  — pin alive; decay suppressed (caller returns False).
        Returns False — pin just expired; stamp refreshed and pin cleared
                        (caller returns False — next beat enters advance flow).
        Returns None  — no pin present; caller continues to anchor check.
        """
        pin = state.user_track_pin
        if pin is None:
            return None
        if not _pin_ttl_expired(pin, now_mono):
            log.debug(
                "state-decay: suppressed — pin active release=%s pos=%s age=%.1fs",
                pin.get("release_id"), pin.get("track_position"), age,
            )
            return True
        # Pin just TTL-expired: refresh stamp and clear so next heartbeat
        # enters predicted-advance flow cleanly instead of flashing NEEDS_ID.
        # See docs/features/pin-expiry-flashes-needs-id/ and
        # docs/features/pin-clearance-no-predicted-advance-at-high-streak/.
        log.info(
            "state-decay: pin TTL expired — refreshing stamp, "
            "clearing pin release=%s pos=%s age=%.1fs streak=%d",
            pin.get("release_id"), pin.get("track_position"),
            age, state.unmatched_streak,
        )
        state.last_vinyl_confidence_set_at = now_mono
        state.user_track_pin = None
        state.pin_different_track_streak = 0
        state.unmatched_streak = NEEDS_ID_STREAK - 1
        return False

    async def _check_state_decay(
        self, audio_source_label: str, level_db: float,
    ) -> bool:
        """Decay stale state.last_vinyl if the last confident recognition is
        older than STATE_DECAY_S.

        Returns True if decay fired (caller should return immediately).
        Returns False if decay was skipped and normal unmatched processing
        should continue.

        Decay is suppressed when:
          - last_vinyl is None (nothing to decay)
          - last_vinyl_confidence_set_at is None (path that set last_vinyl
            was not a vinyl-recognition path, e.g. Sonos airplay/streaming)
          - age < STATE_DECAY_S (recognition is still fresh)
          - user_track_pin is active and TTL not expired (user-authoritative)
          - fingerprint_anchor is active and TTL not expired (strong
            ground-truth match that blocks predicted-advance)
            Note: when fingerprint_anchor has duration_seconds=None the anchor
            never expires via TTL and will indefinitely suppress decay — this
            mirrors the existing _fingerprint_anchor_ttl_expired behaviour and
            is intentional (no-duration anchors are treated as permanent).
        """
        state = self.state
        if state.last_vinyl is None:
            return False
        stamped_at = state.last_vinyl_confidence_set_at
        if stamped_at is None:
            return False
        now_mono = asyncio.get_running_loop().time()
        age = now_mono - stamped_at
        # Decay when the track's expected play time is up — track-remaining ≤ 0
        # when a duration is known, else the flat STATE_DECAY_S backstop. This
        # lets a predicted/guessed track persist for its real length instead of
        # a flat ~45s. See docs/features/guess-decay-on-track-remaining/.
        if not _track_lifetime_elapsed(state, age):
            return False
        # Coexistence: active pin suppresses decay (user is authoritative).
        pin_result = self._decay_pin_check(state, now_mono, age)
        if pin_result is not None:
            return False
        # Coexistence: active fingerprint anchor suppresses decay.
        anchor = state.fingerprint_anchor
        if anchor is not None and not _fingerprint_anchor_ttl_expired(anchor, now_mono):
            log.debug(
                "state-decay: suppressed — anchor active release=%s pos=%s age=%.1fs",
                anchor.get("release_id"), anchor.get("track_position"), age,
            )
            return False
        log.info(
            "state-decay: last_vinyl=%r past expected track end "
            "(stamp age=%.1fs) — forcing needs-id",
            (state.last_vinyl or {}).get("title"), age,
        )
        # Reset streak/stamps before publishing — the decay path bypasses
        # the normal streak machine and neither _try_advance_prediction nor
        # _publish_needs_id resets these stamps internally.
        state.unmatched_streak = 0
        state.last_vinyl_confidence_set_at = None
        state.last_shazam_match_unix_ts = None
        state.last_pin_unix_ts = None
        clear_first_miss_after_match(state)
        # _publish_needs_id internally routes through predicted-advance
        # when state.pending_guess is set — see its docstring. Both this
        # path (state-decay) and the streak path benefit from the
        # internal routing without duplicating it at call sites.
        await self._publish_needs_id(audio_source_label, level_db)
        return True
