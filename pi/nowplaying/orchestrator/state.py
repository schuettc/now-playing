"""Mutable shared state between Sonos and vinyl tasks."""
from __future__ import annotations

import asyncio
import collections


class State:  # skylos: ignore SKY-Q501 — State is intentionally the orchestrator's single state container. Splitting it would just scatter mutations across multiple objects passed through every method signature, which is the opposite of the design (one canonical state, mutated in one place per concern). Same rationale as the Orchestrator class's SKY-Q501 suppress.
    """Mutable shared state between Sonos and vinyl tasks."""

    def __init__(self) -> None:
        self._init_sonos()
        self._init_idle()
        self._init_vinyl()
        self._init_recognition()
        self._init_pin()
        self._init_fingerprint()
        self._init_guess()
        self._init_llm_context()

    def _init_sonos(self) -> None:
        self.sonos_source: str = "unknown"
        self.sonos_state: str = "STOPPED"
        self.sonos_has_metadata: bool = False

    def _init_idle(self) -> None:
        self.idle_task: asyncio.Task | None = None
        # Set to the source string ("streaming" / "airplay" / "vinyl")
        # when the idle timer has fired and STOPPED was published.
        # While set, on_sonos_event short-circuits on incoming events
        # whose (source, state, title) still match the idled scenario,
        # so Sonos's ~15s repoll cadence can't yank us out of idle.
        self.idled_source: str | None = None
        # Title that was on screen when idle fired. Used by the
        # sticky-idle short-circuit to detect "user scrubbed to a
        # different track while paused" — that should wake the kiosk
        # even though state is still PAUSED. Kept SEPARATE from
        # last_vinyl (cleared by the idle helper) so the comparison
        # doesn't always evaluate None == title.
        self.idled_title: str | None = None

    def _init_vinyl(self) -> None:
        self.last_vinyl: dict | None = None
        self.capture_emit_paused: bool = False
        # Monotonic timestamp (asyncio loop time) of the most recent confident
        # recognition that set last_vinyl. Stamped at Shazam+release hits,
        # F3/F4 fingerprint hits, and user identify/select-release. Used by the
        # state-decay path to clear last_vinyl after STATE_DECAY_S of silence.
        # None means last_vinyl was set by a path that did not stamp confidence
        # (e.g. Sonos airplay/streaming) — those are never decayed.
        # See docs/features/state-decay-when-stale/.
        self.last_vinyl_confidence_set_at: float | None = None

    def _init_recognition(self) -> None:
        # Recent Shazam hits that lacked a Discogs catalog match. Used for
        # cross-heartbeat agreement before publishing a degraded result.
        self.pending_shazam_only: list[tuple[str, str, float]] = []
        # Consecutive unmatched heartbeats since the last confirmed match.
        # Drives the music-level NEEDS_ID prompt and the surface-noise idle timer.
        self.unmatched_streak: int = 0
        # ISO-8601 timestamp anchored at the most recent track-identity change.
        self.track_started_at: str | None = None
        # (release_id, track_position) of the last published identity.
        self.last_published_identity: tuple = (None, None)
        # Unix wall-clock timestamp of the most recent Shazam-confirmed
        # match against the currently-locked release. Stamped ONLY by
        # `_publish_shazam_match` (NOT by predicted-advance refreshes,
        # NOT by fingerprint hits) so it represents a reliable
        # track-boundary signal. Used by `_schedule_pin_backfill`'s
        # predicted-transition path to backfill the unmatched clips
        # between the last Shazam-confirmed track and a mid-side pin.
        # Cleared alongside `last_vinyl_confidence_set_at` on
        # source-flip / album-lock-change / idle.
        # See docs/features/pin-backfill-from-predicted-transition/.
        self.last_shazam_match_unix_ts: int | None = None
        # Unix wall-clock timestamp of the FIRST heartbeat that returned
        # unmatched after the most recent Shazam-confirmed match. Marks
        # the boundary where the prior track stopped being recognized —
        # i.e. real new-track-start is within a few seconds of this ts.
        # Consumed by the pin endpoint to backdate initial_track_position_s
        # and track_started_at when a pin lands after predicted-advance
        # (which fires only after NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S
        # ~= 30s of misses). Cleared alongside last_shazam_match_unix_ts.
        # See docs/features/pin-position-ignores-predicted-advance-latency/.
        self.last_unmatched_after_match_unix_ts: int | None = None

    def _init_pin(self) -> None:
        # Narrow per-track pin written by control.identify_clip. Honored
        # at the top of on_heartbeat's confirm path so the user's
        # Wrong-Track pick survives transient Shazam mis-IDs on
        # sample-heavy albums. Released on strong signals only — see
        # docs/features/manual-override-track-pin/. Shape:
        #   {release_id: int, track_position: str,
        #    monotonic_ts: float, duration_seconds: int | None}
        self.user_track_pin: dict | None = None
        # Count of consecutive Shazam hits within the pinned release on
        # a position other than the pin's. Resets on confirmed-pin hits
        # or when the pin is set/released.
        self.pin_different_track_streak: int = 0
        # Unix wall-clock timestamp of the most recent user pin landed
        # via `_apply_user_track_pin`. Combined with
        # `last_shazam_match_unix_ts` (whichever is more recent) by
        # `_schedule_pin_backfill`'s predicted-transition path to bound
        # the backfill window. Without this, chained pins on the same
        # album lock anchor to the stale Shazam-confirm timestamp and
        # the backfill window spans every intervening track's audio.
        # Cleared alongside `last_shazam_match_unix_ts` on
        # source-flip / album-lock-change / idle.
        # See docs/features/backfill-boundary-uses-stale-shazam-ts/.
        self.last_pin_unix_ts: int | None = None

    def _init_fingerprint(self) -> None:
        # Set when a blind (F4) fingerprint match exceeds the strong-confidence
        # threshold (MIN_FINGERPRINT_HITS * STRONG_FINGERPRINT_ANCHOR_MULTIPLIER).
        # While active and TTL not expired, _handle_unmatched_music_level
        # suppresses predicted-advance exactly as user_track_pin does.
        # Shape mirrors user_track_pin:
        #   {release_id: int, track_position: str,
        #    monotonic_ts: float, hits: int, duration_seconds: int | None}
        # See docs/features/blind-anchor-respects-predicted-advance/.
        self.fingerprint_anchor: dict | None = None
        # Rolling window of recent fingerprint hit entries for LLM context.
        # Each entry: {position: str, hits: int, ts: float (monotonic)}.
        # Capped at 10 entries; pruned by _handle_unmatched_music_level.
        # Cleared on idle and album-lock change.
        # See docs/features/llm-track-change-primary/.
        self.recent_fp_hits: list[dict] = []

    def _init_guess(self) -> None:
        # Tracklist-aware advancement state. Set when we predict the
        # current track from the locked album's tracklist on a Shazam
        # miss. Kept SEPARATE from last_vinyl so a miss never pollutes
        # the confirmed lock. Shape:
        #   {release_id: int, side: str, track_position: str, index_in_side: int}
        # See docs/features/tracklist-aware-advancement/.
        self.predicted_position: dict | None = None
        # Nested `guess` object stash. Set on a Shazam-miss + fingerprint-
        # miss heartbeat by the orchestrator's `_compute_track_guess`
        # helper; attached and cleared in `_anchor_and_publish` (single
        # canonical point of consumption). Belt-and-suspenders cleared
        # in idle transitions too. Shape per `kiosk/src/types.ts::Guess`:
        #   {position, title, confidence, source, alt?}
        # See docs/features/llm-track-guess/.
        self.pending_guess: dict | None = None
        # Guesses the user has rejected via `POST /api/dismiss-guess`,
        # keyed by `(release_id, track_position) -> monotonic_ts`.
        # `_compute_track_guess` consults this set and returns None
        # for any (rid, pos) still within `DISMISSED_GUESS_TTL_S`.
        # Cleared on album-lock change so a re-played album later
        # gets fresh chances. See docs/features/identify-guess-confirm/.
        self.dismissed_guesses: dict[tuple[int, str], float] = {}

    def _init_llm_context(self) -> None:
        # Most recent Shazam result seen by the orchestrator, stored even when
        # gated (low-confidence or no catalog match). Cleared on idle and
        # album-lock change. Shape: {artist, title, release_id (nullable),
        # confidence (nullable)}. Used as LLM context for track-change decisions.
        # See docs/features/llm-track-change-primary/.
        self.last_shazam_gated: dict | None = None
        # Rolling list of audible/silent edge events in the last 60s.
        # Each entry: {type: "audible"|"silent", ts_iso: str}.
        # Pruned to 60s window in _handle_capture_audible/_handle_capture_silent.
        # Cleared on idle. See docs/features/llm-track-change-primary/.
        self.recent_audible_edges: list[dict] = []
        # Monotonic timestamp of the most recent "audible" edge — i.e. the
        # last time the capture daemon saw silence→audio transition. Distinct
        # from `recent_audible_edges` (pruned to 60s) in that this holds
        # arbitrarily-old timestamps and answers "how long since the user
        # dropped the needle". Powers `_compute_elapsed_since_audible_up_s`
        # so the LLM track-guess hook can distinguish "side just started"
        # from "last confirmed track was 30s ago." Cleared on idle / source
        # change. See docs/features/llm-track-guess-elapsed-frame-confusion/.
        self.audible_up_at_mono: float | None = None
        # Track positions recognized (Shazam-confirmed or predicted-advanced)
        # since the last audible-edge fired. The fresh-side-first-track gate
        # consults this to detect when audio context already contains audio
        # from a DIFFERENT track than the pin — in which case the wide
        # needle-drop boundary would sweep in prior-track audio and mis-label
        # it. Reset to set() on audible-edge (needle drop) and cleared on
        # source-flip / idle. See
        # docs/features/first-track-gate-misses-predicted-advance/.
        self.tracks_seen_since_audible_edge: set[str] = set()
        # Rolling deque of recent heartbeat level_db readings. Pushed by
        # on_heartbeat before recognition runs. Powers the dead-air
        # suppression gate in _compute_track_guess. Cleared on idle and
        # source-flip alongside the other rolling fields above.
        # See docs/features/llm-track-guess-suppress-on-dead-air/.
        self.recent_heartbeat_levels: collections.deque[float] = collections.deque(maxlen=5)
