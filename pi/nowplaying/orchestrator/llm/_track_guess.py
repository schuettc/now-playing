"""LLM-assisted track-guess proposer + side-flip detection.

Extracted from ``_llm_hooks.py``.  Owns the full ``_compute_track_guess``
call chain: elapsed helpers, dead-air suppression, side-flip detection,
LLM and heuristic branches.
See docs/features/llm-track-guess/ for the design.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from nowplaying import history
from nowplaying.orchestrator.guess import _guess_is_dismissed

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


def _next_side_in_progression(
    locked_side: str, other_sides: list[str],
) -> str:
    """Return the next side in the record's physical progression.

    Sort all available sides lexicographically (A, B, C, D, ...) and
    return the side immediately after ``locked_side``. Returns ``""``
    when ``locked_side`` is the last side of the record — that's the
    end-of-album signal: there IS no next side to flip to.

    Examples:
      A | [B]        → B     (1-LP, end of A)
      B | [A]        → ""    (1-LP, record over)
      A | [B, C, D]  → B     (2-LP, end of A)
      B | [A, C, D]  → C     (2-LP, end of B)
      D | [A, B, C]  → ""    (2-LP, record over)

    See docs/features/llm-track-guess-side-progression-not-flip/.
    """
    if not locked_side:
        return ""
    all_sides = sorted({locked_side, *other_sides})
    idx = all_sides.index(locked_side)
    if idx + 1 >= len(all_sides):
        return ""
    return all_sides[idx + 1]


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

    # Threshold constants for the side-flip-detection signal.
    # See docs/features/llm-track-guess-side-flip-detection/.
    _FLIP_FRESH_AUDIBLE_UP_S = 30.0
    _FLIP_DEEP_INTO_SIDE_FRAC = 0.5
    # Threshold constants for the dead-air suppression gate.
    # See docs/features/llm-track-guess-suppress-on-dead-air/.
    _DEAD_AIR_MIN_AUDIBLE_UP_S = 60.0
    _DEAD_AIR_MIN_UNMATCHED_STREAK = 3
    _DEAD_AIR_LEVEL_DB_AVG_MAX = -6.0  # loosened from -8 after live data
    # 2026-05-22 showed YPAA flip windows hovering around -7 dB (groove
    # noise + needle-lift + new-side-drop) without the average ever
    # dipping below -8. -6 keeps the gate clear of true music levels
    # (typically -3 or louder) while catching the side-flip transition.
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
    # End-of-side level-aware gate: when locked at last-on-side we have
    # a strong prior that any unmatched audio is runout groove noise
    # (clicks, rumble, RIAA amplification of empty groove). True music
    # rarely averages below -3 dB while runout typically lands at -3 to
    # -7 dB. The general dead_air gate uses -6 (tuned for quiet flip
    # windows mid-album); here we can be much more lenient. Observed
    # Black Parade runout 2026-05-26 15:31–15:32 averaged -4.2 dB —
    # the general -6 gate didn't fire but a -3 gate at end-of-side
    # would have.
    _END_OF_SIDE_LEVEL_DB_AVG_MAX = -3.0

    @staticmethod
    def _compute_elapsed_since_last_confirm_s(track_started_at: str | None) -> float:
        """Seconds since the most recent confirmed track anchor
        (Shazam/fingerprint/user-pin/predicted-advance). Reads
        ``state.track_started_at`` (ISO-8601 with Z).

        This clock RESETS on every predicted-advance — meaning "how long
        ago did we last positively identify a track," NOT "how long has
        the side been playing." For the latter, use
        ``_compute_elapsed_since_audible_up_s``.

        Returns 0.0 on missing or unparseable input.
        See docs/features/llm-track-guess-elapsed-frame-confusion/.
        """
        if not track_started_at:
            return 0.0
        try:
            anchor = datetime.fromisoformat(
                track_started_at.replace("Z", "+00:00"),
            )
            return (datetime.now(timezone.utc) - anchor).total_seconds()
        except (ValueError, AttributeError) as e:
            log.warning(
                "track-guess: unparseable track_started_at=%r (%s); using 0",
                track_started_at, e,
            )
            return 0.0

    @staticmethod
    def _compute_elapsed_since_audible_up_s(audible_up_at_mono: float | None) -> float:
        """Seconds since the most recent silent→audible edge — i.e.
        "how long has the side been playing since the user dropped the
        needle." Distinct from ``_compute_elapsed_since_last_confirm_s``
        because this clock SURVIVES mid-side predicted-advance refreshes
        of ``track_started_at``.

        Returns 0.0 when ``audible_up_at_mono`` is None (no needle drop
        recorded yet this session, or cleared by idle / source change).
        See docs/features/llm-track-guess-elapsed-frame-confusion/.
        """
        if audible_up_at_mono is None:
            return 0.0
        return asyncio.get_event_loop().time() - audible_up_at_mono

    # Backwards-compat alias for any external callers — prefer the
    # explicitly-named helpers above. Remove once no callers reference
    # the old name. See same idea.md for the rename rationale.
    @classmethod
    def _compute_side_elapsed_s(cls, track_started_at: str | None) -> float:
        return cls._compute_elapsed_since_last_confirm_s(track_started_at)

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
    def _build_llm_guess_obj(verdict, title_for) -> dict:
        """Project an LLM verdict + title-resolver into the kiosk Guess shape."""
        title = title_for(verdict.position)
        if not title:
            log.warning(
                "track-guess: LLM picked position=%r not in tracklist; "
                "publishing with empty title",
                verdict.position,
            )
        guess_obj: dict = {
            "position": verdict.position,
            "title": title,
            "confidence": verdict.confidence,
            "source": "llm",
        }
        if verdict.alt and verdict.confidence == "medium":
            alt_pos = verdict.alt.get("position")
            if alt_pos:
                guess_obj["alt"] = {
                    "position": alt_pos,
                    "title": title_for(alt_pos),
                }
        return guess_obj

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

    @staticmethod
    def _resolve_next_side_first(  # skylos: ignore SKY-Q301 — Why: CC 12 comes from two successive filter-and-sort steps (other sides → progression sort → target tracks); all branches are early-exit guards on the same linear algorithm
        all_tracks: list[dict],
        locked_side: str,
    ) -> dict | None:
        """Return ``{position, title, side}`` for the first track on the
        next side in the record's physical progression, or None when the
        locked side is the last side of the record (record over) or no
        other side exists in the catalog.

        See docs/features/llm-track-guess-side-progression-not-flip/.
        """
        other_tracks = [
            t for t in all_tracks if t.get("side") != locked_side
        ]
        if not other_tracks:
            return None
        other_sides = sorted({t.get("side") for t in other_tracks if t.get("side")})
        if not other_sides:
            return None
        target_side = _next_side_in_progression(locked_side, other_sides)
        if not target_side:
            return None
        target_tracks = [t for t in other_tracks if t.get("side") == target_side]
        if not target_tracks:
            return None
        first = target_tracks[0]
        return {
            "position": first.get("track_position") or first.get("position"),
            "title": first.get("title"),
            "side": target_side,
        }

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
        if sum(levels) / len(levels) >= self._DEAD_AIR_LEVEL_DB_AVG_MAX:
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
        return avg_level < self._END_OF_SIDE_LEVEL_DB_AVG_MAX

    def _compute_likely_flip(
        self,
        state: "State",
        elapsed_since_audible_up_s: float,
    ) -> tuple[bool, dict | None]:
        """Decide whether the user likely just flipped the record.

        Signal is True iff:
          - The audible-up clock is fresh (< 30s since needle dropped).
          - The last confirmed track was deep into its side (past the 50%
            cumulative-duration mark).
          - The catalog has both sides AND we can resolve the opposite
            side's first track.

        Returns ``(likely_flip, next_side_first)`` where
        ``next_side_first`` is ``{position, title, side}`` for the next
        side's opener (per physical record progression), or None when
        the signal is False / can't resolve / the record is over.

        See docs/features/llm-track-guess-side-progression-not-flip/.
        """
        if elapsed_since_audible_up_s >= self._FLIP_FRESH_AUDIBLE_UP_S:
            return (False, None)
        locked = state.last_vinyl or {}
        locked_side = locked.get("side")
        locked_pos = locked.get("track_position")
        if not locked_side or not locked_pos:
            return (False, None)
        all_tracks = self._load_locked_tracks(state)
        if not all_tracks:
            return (False, None)
        if not self._last_confirm_is_deep_into_side(
            all_tracks, locked_side, locked_pos,
            self._FLIP_DEEP_INTO_SIDE_FRAC,
        ):
            return (False, None)
        next_side_first = self._resolve_next_side_first(all_tracks, locked_side)
        if next_side_first is None:
            return (False, None)
        return (True, next_side_first)

    async def _try_llm_track_guess(
        self,
        state: "State",
        locked_rid,
        locked_side,
        side_tracklist: list,
        title_for,
    ) -> tuple[bool, dict | None]:
        """LLM-judged track-guess branch.

        Returns ``(llm_decided, guess)`` where ``llm_decided`` is True iff
        the LLM produced a verdict (caller stops here — even when
        ``guess`` is None because the user dismissed it). False means the
        LLM returned the heuristic sentinel and the caller should fall
        through to the heuristic path.

        Caller is responsible for the `self.llm.enabled and side_tracklist`
        gate.
        """
        from nowplaying.llm import USE_HEURISTIC

        # Two separately-named clocks (see
        # docs/features/llm-track-guess-elapsed-frame-confusion/):
        #   - elapsed_since_audible_up: "how long since the needle dropped"
        #     (survives mid-side predicted-advance refreshes)
        #   - elapsed_since_last_confirm: "how long since the last positive
        #     ID" (resets on every confirmed-track anchor)
        elapsed_since_audible_up_s = self._compute_elapsed_since_audible_up_s(
            state.audible_up_at_mono,
        )
        elapsed_since_last_confirm_s = self._compute_elapsed_since_last_confirm_s(
            state.track_started_at,
        )
        recent_history: list[dict] = []
        try:
            recent_history = await asyncio.to_thread(history.recent, 5)
        except Exception as e:  # noqa: BLE001  # Why — history is optional; degrade gracefully
            log.warning("track-guess: history.recent failed: %r", e)
        predicted_pos = (
            state.predicted_position.get("track_position")
            if state.predicted_position is not None else None
        )
        likely_flip, next_side_first = self._compute_likely_flip(
            state, float(elapsed_since_audible_up_s),
        )
        verdict = await self.llm.judge_track_guess(
            locked_album_ctx={
                "locked_artist": state.last_vinyl.get("artist"),
                "locked_album": state.last_vinyl.get("album"),
                "locked_release_id": state.last_vinyl.get("release_id"),
                "locked_side": locked_side,
                "locked_title": state.last_vinyl.get("title"),
            },
            side_tracklist=side_tracklist,
            recent_history=recent_history,
            audible_up_iso=state.track_started_at,
            elapsed_since_audible_up_s=float(elapsed_since_audible_up_s),
            elapsed_since_last_confirm_s=float(elapsed_since_last_confirm_s),
            predicted_position=predicted_pos,
            likely_flip=likely_flip,
            next_side_first=next_side_first,
        )
        if verdict is USE_HEURISTIC:
            return (False, None)
        if self._guess_is_dismissed_for(state, locked_rid, verdict.position):
            log.info(
                "track-guess: LLM verdict pos=%s suppressed (dismissed by user)",
                verdict.position,
            )
            return (True, None)
        guess_obj = self._build_llm_guess_obj(verdict, title_for)
        log.info(
            "track-guess: LLM picked position=%s confidence=%s alt=%s — %s",
            verdict.position, verdict.confidence,
            guess_obj.get("alt"), verdict.reason,
        )
        return (True, guess_obj)

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
        if self.llm.enabled and side_tracklist:
            llm_decided, guess = await self._try_llm_track_guess(
                state, locked_rid, locked_side, side_tracklist, title_for,
            )
            if llm_decided:
                return guess  # dict (publish) or None (dismissed)
        return self._try_heuristic_track_guess(state, locked_rid, title_for)
