"""Shazam-only gate, pin application, and idle timer.

Contains: _shazam_only_gate_passes, _record_pending_shazam_agreement,
_shazam_level_gate, _apply_pin_decision, _idle_after_delay.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import recognize_proto

from nowplaying.orchestrator._first_miss import clear_first_miss_after_match
from nowplaying.orchestrator.pin import (
    PIN_DIFFERENT_TRACK_RELEASE_STREAK,
    _evaluate_user_pin,
    _pin_ttl_expired,
)
from nowplaying.orchestrator.streaming_idle import (
    SHAZAM_ONLY_MIN_LEVEL_DB,
    _is_music_level,
)

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


class _ShazamGateMixin:
    """Shazam-only gate, pin application, idle.  Mixed into PredictionMixin."""

    def _shazam_only_gate_passes(
        self, level_db: float, clip_path: Path, result: dict,
    ) -> bool:
        """Cross-heartbeat agreement gate for Shazam matches that don't
        resolve to a release in the local Discogs catalog. Returns True
        when the same (artist,title) has been observed MIN_AGREEMENTS times
        within PENDING_WINDOW_S, False to drop this heartbeat silently.
        Fully relaxed for ``_instant`` clips (user-triggered fresh capture):
        both the level gate and the agreement gate are bypassed so a
        user-initiated "Identify Now" scan publishes on the first attempt.
        The hit is still recorded in the agreement window so it can
        contribute to the count for the next regular heartbeat.
        """
        MIN_AGREEMENTS = 2
        if not self._shazam_level_gate(level_db, clip_path):
            return False
        norm_artist = (result.get("artist") or "").strip().lower()
        norm_title = (result.get("title") or "").strip().lower()
        if not norm_artist or not norm_title:
            return False
        # Record this hit regardless of whether we're on an instant clip —
        # it contributes to the agreement window for subsequent heartbeats.
        matches = self._record_pending_shazam_agreement(norm_artist, norm_title)
        # _instant.wav clips bypass the agreement check: the user explicitly
        # triggered a scan and expects an immediate result.
        if clip_path.name.endswith("_instant.wav"):
            log.info(
                "shazam-only confirmed (instant): artist=%r title=%r",
                result.get("artist"), result.get("title"),
            )
            return True
        if matches < MIN_AGREEMENTS:
            log.info(
                "shazam-only pending: artist=%r title=%r agreements=%d/%d",
                result.get("artist"), result.get("title"),
                matches, MIN_AGREEMENTS,
            )
            return False
        log.info(
            "shazam-only confirmed: artist=%r title=%r (no Discogs match)",
            result.get("artist"), result.get("title"),
        )
        return True

    def _record_pending_shazam_agreement(
        self, norm_artist: str, norm_title: str,
    ) -> int:
        """Append this normalized (artist,title) hit to pending_shazam_only,
        purge entries older than the 120s window, and return the count of
        entries currently agreeing on (norm_artist, norm_title).
        """
        PENDING_WINDOW_S = 120.0
        state = self.state
        now_mono = asyncio.get_running_loop().time()
        state.pending_shazam_only = [
            e for e in state.pending_shazam_only
            if now_mono - e[2] < PENDING_WINDOW_S
        ]
        state.pending_shazam_only.append((norm_artist, norm_title, now_mono))
        return sum(
            1 for (a, t, _) in state.pending_shazam_only
            if a == norm_artist and t == norm_title
        )

    @staticmethod
    def _shazam_level_gate(level_db: float, clip_path: Path) -> bool:
        """Pre-gate the shazam-only flow on audio level. Relaxed for
        ``_instant`` clips (user-triggered fresh capture). Returns True to
        proceed with the cross-heartbeat agreement check, False to drop.
        """
        if clip_path.name.endswith("_instant.wav"):
            log.info(
                "shazam-only gate relaxed: _instant clip (level_db=%.1f)",
                level_db,
            )
            return True
        if not _is_music_level(level_db):
            log.info(
                "shazam-only rejected: level_db=%.1f below %.1f — likely noise",
                level_db, SHAZAM_ONLY_MIN_LEVEL_DB,
            )
            return False
        return True

    def _apply_pin_decision(
        self, payload: dict, rid: int | None, result: dict,
    ) -> None:
        """Evaluate the user-track pin against this recognition and mutate
        `payload` in place: clear the pin, honor it (overwrite payload fields
        from the pinned state.last_vinyl), or leave it alone.
        """
        state = self.state
        pin_now_mono = asyncio.get_running_loop().time()
        action, new_streak, reason = _evaluate_user_pin(
            state.user_track_pin,
            state.pin_different_track_streak,
            rid,
            result.get("track_position"),
            pin_now_mono,
        )
        state.pin_different_track_streak = new_streak
        if action == "clear":
            cleared = state.user_track_pin
            state.user_track_pin = None
            state.fingerprint_anchor = None
            log.info(
                "pin released: reason=%s pinned=%s shazam_rid=%s shazam_pos=%s",
                reason,
                (cleared or {}).get("track_position"),
                rid, result.get("track_position"),
            )
            return
        if action != "honor" or state.last_vinyl is None:
            return
        pinned = state.last_vinyl
        for fld in (
            "release_id", "track_position", "title", "side",
            "artist", "album", "year", "label", "catno",
            "duration_seconds", "tracklist", "track_started_at",
            "art_url",
        ):
            if fld in pinned and pinned.get(fld) is not None:
                payload[fld] = pinned[fld]
        payload["match_method"] = "user-identified"
        payload["match_confidence"] = "user"
        payload.pop("alternate_releases", None)
        log.info(
            "pin honored: kept=%r shazam-said=%r reason=%s pos=%s shazam_pos=%s streak=%d/%d",
            pinned.get("title"),
            result.get("title"),
            reason,
            pinned.get("track_position"),
            result.get("track_position"),
            new_streak, PIN_DIFFERENT_TRACK_RELEASE_STREAK,
        )

    async def _idle_after_delay(self, delay_s: float, source: str) -> None:
        """Unified idle timer for both the vinyl silence-driven path
        (45s) and the streaming / AirPlay pause-driven path (10min).
        Caller picks `delay_s` + `source`; helper sleeps, then stamps
        sticky-idle fields and publishes STOPPED.
        """
        state = self.state
        bcast = self.bcast
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            log.debug("idle timer cancelled before delay completed (source=%s)", source)
            return
        log.info(
            "idle timer fired: source=%s delay=%.0fs", source, delay_s,
        )
        # Stamp the sticky-idle fields BEFORE clearing last_vinyl —
        # the sticky-idle short-circuit needs idled_title to remember
        # "what was on screen when we went idle" so it can detect
        # title-changed events that should wake us. Pulling from
        # last_vinyl while it's still populated is what makes that
        # work; reversing the order would leave idled_title=None and
        # break the title comparison.
        state.idled_source = source
        state.idled_title = (state.last_vinyl or {}).get("title")
        state.last_vinyl = None
        state.last_vinyl_confidence_set_at = None
        state.last_shazam_match_unix_ts = None
        state.last_pin_unix_ts = None
        clear_first_miss_after_match(state)
        state.predicted_position = None
        # Defensive: idle without a subsequent publish would leak a stale guess.
        state.pending_guess = None
        state.pending_shazam_only.clear()
        state.dismissed_guesses.clear()
        if state.user_track_pin is not None:
            log.info("pin released: reason=idle")
        state.user_track_pin = None
        state.pin_different_track_streak = 0
        state.fingerprint_anchor = None
        state.recent_fp_hits.clear()
        state.last_shazam_gated = None
        state.recent_audible_edges.clear()
        state.tracks_seen_since_audible_edge.clear()
        state.audible_up_at_mono = None
        state.recent_heartbeat_levels.clear()
        # Source badge stays accurate by tagging STOPPED with the
        # source we idled from.
        await bcast.publish({
            "ts": recognize_proto.now_iso(),
            "state": "STOPPED",
            "source": source,
            "match_method": "unmatched",
        })
