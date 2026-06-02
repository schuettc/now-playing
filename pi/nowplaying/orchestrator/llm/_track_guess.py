"""LLM-assisted track-guess proposer + side-flip detection.

Extracted from ``_llm_hooks.py``.  Owns the full ``_compute_track_guess``
call chain: elapsed helpers, dead-air suppression, side-flip detection,
LLM and heuristic branches.
See docs/features/llm-track-guess/ for the design.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nowplaying.orchestrator.guess import _guess_is_dismissed
from nowplaying.vinyl.levels import MUSIC_DB

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


def _cum_duration_at(
    side_tracks: list[dict], locked_pos: str,
) -> tuple[float, float] | None:
    """Return ``(cum_at_locked, total)`` for cumulative duration math,
    or None when ``locked_pos`` is not found on the side."""
    total = 0.0
    cum_at_locked: float | None = None
    for t in side_tracks:
        total += float(t.get("duration_seconds") or 0)
        pos = t.get("track_position") or t.get("position")
        if pos == locked_pos and cum_at_locked is None:
            cum_at_locked = total
    if cum_at_locked is None:
        return None
    return (cum_at_locked, total)


def _cum_start_s(side_tracks: list[dict], pos: str) -> float | None:
    """Cumulative start offset (seconds from the side's start) of ``pos`` on
    the side, summing the durations of the tracks before it. None when
    ``pos`` isn't on the side."""
    s = 0.0
    for t in side_tracks:
        p = t.get("track_position") or t.get("position")
        if p == pos:
            return s
        s += float(t.get("duration_seconds") or t.get("duration_s") or 0)
    return None


def _position_for_side_offset(
    side_tracks: list[dict], offset_s: float,
) -> str | None:
    """Return the track_position whose cumulative ``[start, end)`` window
    contains ``offset_s`` (seconds from the side's start), or None when the
    side has no tracks. This is the deterministic interval lookup that
    replaces the LLM's (unreliable) position arithmetic.

    A track with unknown/zero duration is treated as containing any offset at
    or after its start (can't bound it). An offset past the summed side
    duration clamps to the last track (the end-of-side gate handles true
    run-out separately).
    """
    cum = 0.0
    last_pos: str | None = None
    for t in side_tracks:
        pos = t.get("track_position") or t.get("position")
        last_pos = pos
        dur = float(t.get("duration_seconds") or t.get("duration_s") or 0)
        if dur <= 0:
            if offset_s >= cum:
                return pos
            return last_pos
        if cum <= offset_s < cum + dur:
            return pos
        cum += dur
    return last_pos


def _is_duration_based_deep(
    side_tracks: list[dict],
    locked_pos: str,
    frac: float,
) -> bool | None:
    """Duration-based depth check for ``_last_confirm_is_deep_into_side``.

    Returns True/False when ALL side tracks have a duration, else None
    (signal: fall through to ordinal check).
    """
    result = _cum_duration_at(side_tracks, locked_pos)
    if result is None:
        return None  # position not found on side
    cum_at_locked, total = result
    all_have_durations = all(
        (t.get("duration_seconds") or 0) > 0 for t in side_tracks
    )
    if not (all_have_durations and total > 0):
        return None  # fall through to ordinal
    return (cum_at_locked / total) > frac


def _is_ordinal_deep(
    side_tracks: list[dict],
    locked_pos: str,
    frac: float,
) -> bool | None:
    """Ordinal (track-N-of-M) depth check for ``_last_confirm_is_deep_into_side``.

    Returns True/False when the position is found, else None.
    End-of-side counts as deep regardless of how many tracks precede it.
    """
    for i, t in enumerate(side_tracks):
        pos = t.get("track_position") or t.get("position")
        if pos == locked_pos:
            return ((i + 1) / len(side_tracks)) > frac
    return None  # not found


class TrackGuessMixin:
    """LLM-assisted track-guess proposer and side-flip detection methods."""

    # Threshold constants for the dead-air suppression gate.
    # See docs/features/llm-track-guess-suppress-on-dead-air/.
    _DEAD_AIR_MIN_AUDIBLE_UP_S = 60.0
    _DEAD_AIR_MIN_UNMATCHED_STREAK = 3
    # Dead-air level gate uses MUSIC_DB (vinyl/levels.py): audio averaging
    # below the music floor is dead air / groove noise, not a track.
    _DEAD_AIR_DEEP_FRAC = 0.6
    _DEAD_AIR_LEVEL_WINDOW = 3
    # End-of-side geometric gate: when locked on the *last* track of the
    # side AND elapsed-since-needle-drop is past the cumulative side
    # duration, the side is over. Vinyl can't loop, so any guess at this
    # point would be physically impossible (e.g. LLM hallucinating "back
    # to A1"). The streak-based predecessor (require 2 unmatched
    # heartbeats) rarely tripped in practice because predicted-advance
    # resets unmatched_streak to 0 on every guess publish — see logs
    # around 2026-05-26 14:23–14:24 where the streak oscillated 1→0→1→0
    # for 60s while the LLM repeatedly hallucinated B1. The geometric
    # check has no such race: once elapsed > cumulative + margin, it
    # stays True. Fallback for tracklists with missing durations: use
    # the streak signal at threshold 1 (we know there's no next track).
    _END_OF_SIDE_MARGIN_S = 10.0
    _END_OF_SIDE_FALLBACK_MIN_UNMATCHED_STREAK = 1
    # End-of-side level gate now also uses MUSIC_DB (vinyl/levels.py): on the
    # clean LINE signal we no longer have the old double-phono-gain that made
    # runout groove read at -3..-7 dB, so the separate, more-lenient end-of-side
    # threshold was dropped — audio averaging below the music floor at
    # last-on-side is treated as runout. Re-introduce a measured const here if
    # LINE runout ever needs distinct handling.

    @staticmethod
    def _compute_elapsed_since_audible_up_s(audible_up_at_mono: float | None) -> float:
        """Seconds since the most recent silent→audible edge — i.e.
        "how long has the side been playing since the user dropped the
        needle." Survives mid-side predicted-advance refreshes of
        ``track_started_at``.

        Returns 0.0 when ``audible_up_at_mono`` is None (no needle drop
        recorded yet this session, or cleared by idle / source change).
        See docs/features/llm-track-guess-elapsed-frame-confusion/.
        """
        if audible_up_at_mono is None:
            return 0.0
        return asyncio.get_event_loop().time() - audible_up_at_mono

    def _guess_is_dismissed_for(
        self, state: "State", locked_rid, position: str,
    ) -> bool:
        """Wrap `_guess_is_dismissed` with the standard (rid, position,
        loop-time) args. Returns False when no release_id (no dismissal set).
        """
        if locked_rid is None:
            return False
        return _guess_is_dismissed(
            state.dismissed_guesses,
            int(locked_rid),
            position,
            asyncio.get_running_loop().time(),
        )

    @staticmethod
    def _last_confirm_is_deep_into_side(
        all_tracks: list[dict],
        locked_side: str,
        locked_pos: str,
        frac: float,
    ) -> bool:
        """True iff the locked position sits past the ``frac`` mark of its
        side. Uses cumulative-duration when durations are populated;
        falls back to position-ordinal (track N of M) when durations
        are missing. The fallback matters for releases that escaped the
        MusicBrainz duration backfill (e.g. Hum *Downward Is Heavenward*
        whose 13 tracks all have ``duration_seconds=NULL``).

        Shared by ``_compute_likely_flip`` (frac=0.5) and the dead-air
        gate (0.6). Returns False only when the position can't be
        located on the side at all.
        """
        side_tracks = [t for t in all_tracks if t.get("side") == locked_side]
        if not side_tracks:
            return False
        duration_result = _is_duration_based_deep(side_tracks, locked_pos, frac)
        if duration_result is not None:
            return duration_result
        ordinal_result = _is_ordinal_deep(side_tracks, locked_pos, frac)
        return ordinal_result if ordinal_result is not None else False

    def _should_suppress_track_guess_for_dead_air(
        self,
        state: "State",
        elapsed_since_audible_up_s: float,
    ) -> bool:
        """True iff all four end-of-side dead-air conditions hold:
          1. ``elapsed_since_audible_up_s`` > 60s (side has been playing)
          2. ``state.unmatched_streak`` >= 3 (multi-heartbeat unmatched run)
          3. average ``level_db`` over the last 3 heartbeats < -6
          4. last confirmed track is past 60% of cumulative side duration

        Any missing input (insufficient heartbeat samples, no tracklist,
        no lock) → False (don't suppress).
        See docs/features/llm-track-guess-suppress-on-dead-air/.
        """
        if elapsed_since_audible_up_s <= self._DEAD_AIR_MIN_AUDIBLE_UP_S:
            return False
        if state.unmatched_streak < self._DEAD_AIR_MIN_UNMATCHED_STREAK:
            return False
        levels = list(state.recent_heartbeat_levels)[-self._DEAD_AIR_LEVEL_WINDOW:]
        if len(levels) < self._DEAD_AIR_LEVEL_WINDOW:
            return False
        if sum(levels) / len(levels) >= MUSIC_DB:
            return False
        locked = state.last_vinyl or {}
        locked_side = locked.get("side")
        locked_pos = locked.get("track_position")
        if not locked_side or not locked_pos:
            return False
        all_tracks = self._load_locked_tracks(state)
        if not all_tracks:
            return False
        return self._last_confirm_is_deep_into_side(
            all_tracks, locked_side, locked_pos, self._DEAD_AIR_DEEP_FRAC,
        )

    @staticmethod
    def _locked_pos_is_last_on_side(
        all_tracks: list[dict], locked_side: str, locked_pos: str,
    ) -> bool:
        """True iff ``locked_pos`` is the final track on ``locked_side``.

        Catalog tracklist is in physical order (Discogs), so the last
        side-filtered entry is the last track on the side. Returns False
        when the side is empty or the position isn't on it.
        """
        side_tracks = [t for t in all_tracks if t.get("side") == locked_side]
        if not side_tracks:
            return False
        last = side_tracks[-1]
        last_pos = last.get("track_position") or last.get("position")
        return last_pos == locked_pos

    def _should_suppress_track_guess_for_end_of_side(  # skylos: ignore SKY-Q301 — Why: two orthogonal end-of-side cases (geometric loop + level-aware runout) gated by the same last-on-side precondition; splitting would duplicate the catalog/last-on-side checks
        self, state: "State",
    ) -> bool:
        """True iff locked at the last track of the side AND either:

          (A) Geometric: elapsed time since the needle dropped is past
              the cumulative side duration (+ small clock-drift margin).
              Catches the side-replay loop case — vinyl can't loop, so
              once we're past the end of the side there's no "next
              track" to guess. Uses the audible-up clock (which
              survives predicted-advance refreshes).

          (B) Level-aware runout: there's at least one unmatched
              heartbeat AND the avg level over the last 3 heartbeats is
              below the lenient end-of-side noise threshold (-3 dB).
              At end-of-side we have a strong prior that any unmatched
              audio is runout groove noise; true music averages above
              -3 dB so this threshold is safe.

        Falls back to a streak-based check (>= 1 unmatched heartbeat)
        when the tracklist has missing durations and case (A) can't be
        evaluated.
        """
        locked = state.last_vinyl or {}
        locked_side = locked.get("side")
        locked_pos = locked.get("track_position")
        if not locked_side or not locked_pos:
            return False
        all_tracks = self._load_locked_tracks(state)
        if not all_tracks:
            return False
        if not self._locked_pos_is_last_on_side(
            all_tracks, locked_side, locked_pos,
        ):
            return False
        # Case A — geometric (loop detector).
        side_tracks = [t for t in all_tracks if t.get("side") == locked_side]
        durations = [
            float(t.get("duration_seconds") or 0) for t in side_tracks
        ]
        if all(d > 0 for d in durations):
            cumulative_s = sum(durations)
            elapsed_s = self._compute_elapsed_since_audible_up_s(
                state.audible_up_at_mono,
            )
            if elapsed_s > cumulative_s + self._END_OF_SIDE_MARGIN_S:
                return True
        elif (
            state.unmatched_streak
            >= self._END_OF_SIDE_FALLBACK_MIN_UNMATCHED_STREAK
        ):
            return True
        # Case B — level-aware runout (catches the in-side case where
        # the user lets the needle spin past the last track but hasn't
        # flipped yet, so audible-up hasn't reset).
        if state.unmatched_streak < 1:
            return False
        levels = list(state.recent_heartbeat_levels)[-self._DEAD_AIR_LEVEL_WINDOW:]
        if len(levels) < self._DEAD_AIR_LEVEL_WINDOW:
            return False
        avg_level = sum(levels) / len(levels)
        return avg_level < MUSIC_DB

    @staticmethod
    def _estimate_side_position_s(
        state: "State",
        side_tracklist: list,
        elapsed_since_audible_up_s: float,
    ) -> float | None:
        """Estimate the current position on the side (seconds from the side's
        start), anchored to the confirmed track so the LLM does a window
        lookup instead of summing durations.

        Pin-anchored when a user pin is live — stable across predicted-advance
        drift (which is what reinforced the racing): ``cum_start(pin pos) +
        initial_track_position_s + pin age``. Otherwise assume the needle
        dropped at the side's first track and use elapsed-since-needle-drop.
        Returns None when neither input is usable.
        """
        pin = getattr(state, "user_track_pin", None)
        if isinstance(pin, dict) and pin.get("track_position"):
            cs = _cum_start_s(side_tracklist, pin["track_position"])
            init = pin.get("initial_track_position_s")
            ts = pin.get("monotonic_ts")
            if cs is not None and init is not None and ts is not None:
                age = asyncio.get_event_loop().time() - float(ts)
                return cs + float(init) + age
        if elapsed_since_audible_up_s and elapsed_since_audible_up_s > 0:
            return float(elapsed_since_audible_up_s)
        return None

    @staticmethod
    def _guard_no_backward_side_pos(
        side_tracklist: list, current_pos: str | None, guessed_pos: str,
    ) -> str:
        """Clamp ``guessed_pos`` so a guess never moves *backward* on the side.

        A record plays in one direction. When the side-position estimate
        collapses (e.g. the needle-drop clock resets and elapsed-since-audible-up
        craters), the window lookup can land on a track earlier than the one
        currently shown — producing a physically impossible backward jump (the
        live B4 'She Hates My Job' → B2 'Lead Pipe Cinch' case). In that case
        hold the current track rather than jumping back; a real Shazam/fingerprint
        hit re-anchors forward when it arrives.

        Returns ``current_pos`` when the guess is earlier on the side, else
        ``guessed_pos``. No-ops when either position isn't on this side
        (e.g. a legitimate side flip) so forward motion and flips are unaffected.
        See docs/features/advance-on-shazam-quiet-records/.
        """
        if not current_pos:
            return guessed_pos
        order = [
            t.get("track_position") or t.get("position") for t in side_tracklist
        ]
        try:
            guessed_i = order.index(guessed_pos)
            current_i = order.index(current_pos)
        except ValueError:
            return guessed_pos  # one isn't on this side — don't guard
        return current_pos if guessed_i < current_i else guessed_pos

    def _try_window_track_guess(
        self,
        state: "State",
        locked_rid,
        side_tracklist: list,
        title_for,
        elapsed_since_audible_up_s: float,
    ) -> dict | None:
        """Deterministic track guess: locate the track whose cumulative
        ``[start, end)`` window contains the confirmed-track-anchored estimated
        side position.

        Replaces the LLM position math, which proved unreliable at the one
        mechanical step that matters here — the interval comparison (it placed
        an estimate of 100s inside A2's [190,367] window and raced the kiosk
        ahead of the music). The estimate itself is anchored to the confirmed
        track (pin-stable). A monotonic guard then prevents the guess from ever
        moving backward on the side. Returns None when there's no estimate or no
        match, so the caller falls through to the predicted_position heuristic.
        See docs/features/advance-on-shazam-quiet-records/.
        """
        est = self._estimate_side_position_s(
            state, side_tracklist, elapsed_since_audible_up_s,
        )
        if est is None:
            return None
        pos = _position_for_side_offset(side_tracklist, est)
        if not pos:
            return None
        current_pos = (state.last_vinyl or {}).get("track_position")
        guarded = self._guard_no_backward_side_pos(side_tracklist, current_pos, pos)
        if guarded != pos:
            log.info(
                "track-guess: window pos=%s would move backward from %s — "
                "holding (est=%.0fs)", pos, current_pos, est,
            )
            pos = guarded
        if self._guess_is_dismissed_for(state, locked_rid, pos):
            log.info(
                "track-guess: window pos=%s suppressed (dismissed by user)", pos,
            )
            return None
        log.info("track-guess: window pos=%s (est=%.0fs)", pos, est)
        return {
            "position": pos,
            "title": title_for(pos),
            "confidence": "high",
            "source": "window",
        }

    def _try_heuristic_track_guess(
        self, state: "State", locked_rid, title_for,
    ) -> dict | None:
        """Heuristic fallback: use the existing predicted_position."""
        if state.predicted_position is None:
            return None
        pos = state.predicted_position.get("track_position")
        if not pos:
            return None
        if self._guess_is_dismissed_for(state, locked_rid, pos):
            log.info(
                "track-guess: heuristic pos=%s suppressed (dismissed by user)",
                pos,
            )
            return None
        return {
            "position": pos,
            "title": title_for(pos),
            "confidence": "low",
            "source": "heuristic",
        }

    @staticmethod
    def _make_title_for(tracklist: list):
        """Build a title-resolver closure over a tracklist.

        Tracklist items use ``position`` in production (built by
        recognize_proto, _tracklist_from_release, _publish_enrichment).
        Some legacy paths use ``track_position``; accept both so the
        resolver works regardless of how last_vinyl was assembled.
        See docs/features/best-guess-empty-title-bug/.
        """
        def _title_for(position: str | None) -> str:
            if not position:
                return ""
            for t in tracklist:
                if not isinstance(t, dict):
                    continue
                if t.get("position") == position or t.get("track_position") == position:
                    return t.get("title") or ""
            return ""
        return _title_for

    @staticmethod
    def _prepare_track_guess_ctx(state: "State") -> tuple:
        """Pull the inputs `_compute_track_guess` needs from state.

        Returns ``(ok, locked_rid, locked_side, tracklist, side_tracklist)``
        where ``ok`` is False (and the remaining fields are None/[]) when
        there's no usable lock/tracklist.
        """
        if state.last_vinyl is None:
            return (False, None, None, [], [])
        tracklist = state.last_vinyl.get("tracklist") or []
        if not tracklist:
            return (False, None, None, [], [])
        locked_rid = state.last_vinyl.get("release_id")
        locked_side = state.last_vinyl.get("side")
        side_tracklist = [
            t for t in tracklist if t.get("side") == locked_side
        ] if locked_side else list(tracklist)
        return (True, locked_rid, locked_side, tracklist, side_tracklist)

    async def _compute_track_guess(self, state: "State") -> dict | None:
        """Build a nested `guess` payload for the locked album, or None.

        Called from `_try_fingerprint_fallback`'s no-hit branch. Tries the
        LLM hook first (gated on `ANTHROPIC_API_KEY`); falls back to the
        heuristic `predicted_position`. Returns None when there's no lock,
        no usable tracklist, or both paths produce nothing.

        Shape per `kiosk/src/types.ts::Guess`:
          {position, title, confidence, source, alt?}

        See docs/features/llm-track-guess/.
        """
        ok, locked_rid, locked_side, tracklist, side_tracklist = (
            self._prepare_track_guess_ctx(state)
        )
        if not ok:
            return None
        # End-of-side dead-air gate: when the side has been playing >60s,
        # we've been unmatched for 3+ heartbeats, the audio is sub-music
        # level, and the locked track is deep into the side, suppress
        # both LLM and heuristic guesses. See
        # docs/features/llm-track-guess-suppress-on-dead-air/.
        elapsed_since_audible_up_s = self._compute_elapsed_since_audible_up_s(
            state.audible_up_at_mono,
        )
        if self._should_suppress_track_guess_for_dead_air(
            state, float(elapsed_since_audible_up_s),
        ):
            levels = list(state.recent_heartbeat_levels)[-self._DEAD_AIR_LEVEL_WINDOW:]
            level_avg = sum(levels) / len(levels) if levels else 0.0
            log.info(
                "track-guess: suppressed reason=dead_air "
                "elapsed_audible_up=%.0fs unmatched_streak=%d level_db_avg=%.1f",
                elapsed_since_audible_up_s, state.unmatched_streak, level_avg,
            )
            return None
        if self._should_suppress_track_guess_for_end_of_side(state):
            # Also drop any stale pending_guess from a prior heartbeat —
            # the NEEDS_ID path consults pending_guess and would republish
            # the old guess as a predicted-advance, keeping the kiosk on
            # a track that's actually over. Once we're confident it's
            # runout, the kiosk should drop to NEEDS_ID rather than show
            # a stale "still playing" timer.
            state.pending_guess = None
            log.info(
                "track-guess: suppressed reason=end_of_side "
                "locked_pos=%s locked_side=%s "
                "elapsed_audible_up=%.0fs unmatched_streak=%d",
                (state.last_vinyl or {}).get("track_position"),
                (state.last_vinyl or {}).get("side"),
                elapsed_since_audible_up_s,
                state.unmatched_streak,
            )
            return None
        title_for = self._make_title_for(tracklist)
        # Deterministic window lookup replaces the LLM position math: the LLM
        # proved unreliable at the interval comparison (two prompt iterations
        # both raced the kiosk ahead of the music). The estimate is anchored to
        # the confirmed track; locating the track from it is a trivial,
        # always-correct interval check done in code.
        # See docs/features/advance-on-shazam-quiet-records/.
        if side_tracklist:
            guess = self._try_window_track_guess(
                state, locked_rid, side_tracklist, title_for,
                float(elapsed_since_audible_up_s),
            )
            if guess is not None:
                return guess
        return self._try_heuristic_track_guess(state, locked_rid, title_for)
