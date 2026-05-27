"""HeartbeatHandlersMixin — heartbeat recognition cascade.

Contains: on_heartbeat, _handle_non_shazam_heartbeat, _publish_shazam_match,
_lookup_fingerprint_hit, _try_fingerprint_fallback,
_try_confirmation_fingerprint, _try_blind_fingerprint, _set_fingerprint_anchor,
_schedule_coverage_promotion, _classify_heartbeat_source,
_retract_pending_idle_for_music, _run_recognizer.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import recognize_proto

# nowplaying.orchestrator._class is imported lazily (at call time) rather than
# at module level to avoid a circular import (_class imports this module).
# Tests patch `nowplaying.orchestrator._class.history` and
# `nowplaying.orchestrator._class._build_fingerprint_payload`; using
# `_oc.history` / `_oc._build_fingerprint_payload` at call time picks up
# those patches correctly.
import nowplaying.orchestrator._class as _oc  # noqa: E402 — deferred circular-safe

from nowplaying.discovery import fingerprint as discovery_fingerprint
from nowplaying.vinyl import fingerprint, promotion
from nowplaying.vinyl.runtime import to_now_playing_vinyl
from nowplaying.orchestrator.fingerprint import (
    _build_blind_fingerprint_payload,
)
from nowplaying.orchestrator.io_helpers import _read_bytes
from nowplaying.orchestrator.pin import (
    _fingerprint_anchor_ttl_expired,
    _pin_ttl_expired,
)
from nowplaying.orchestrator.streaming_idle import (
    MIN_FINGERPRINT_HITS_ANCHORED,
    MIN_FINGERPRINT_HITS_BLIND,
    STRONG_FINGERPRINT_ANCHOR_MULTIPLIER,
    _is_music_level,
)

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


def _cascade_match_dispatch(
    wav_bytes: bytes,
    locked_rid: int | None,
    locked_mbid: str | None,
) -> list[fingerprint.Hit]:
    """Route a fingerprint match to the right store(s) based on lock shape.

    - Discogs lock (``locked_rid`` set) → scoped scan of ``fingerprint.db``.
    - MBID lock (``locked_mbid`` set, no rid) → scoped scan of
      ``discovered.sqlite``.
    - Both ``None`` (blind) → unioned scan over BOTH stores; results
      sorted by ``Hit.hits`` descending so the strongest hit across either
      store wins the runner-up margin gate.

    Runs in a thread (called via ``asyncio.to_thread``). Both DB modules
    swallow missing-file cases by returning ``[]``.
    """
    if locked_rid is not None:
        return fingerprint.match(wav_bytes, int(locked_rid))
    if locked_mbid is not None:
        return discovery_fingerprint.match(wav_bytes, locked_mbid)
    discogs_hits = fingerprint.match(wav_bytes, None)
    discovered_hits = discovery_fingerprint.match(wav_bytes, None)
    if not discovered_hits:
        return discogs_hits
    if not discogs_hits:
        return discovered_hits
    merged = [*discogs_hits, *discovered_hits]
    merged.sort(key=lambda h: h.hits, reverse=True)
    return merged


class HeartbeatHandlersMixin:
    """Heartbeat recognition cascade (Shazam → fingerprint → unmatched).

    All state is accessed via ``self.state``, ``self.bcast``,
    ``self.fingerprint_enabled``, and ``self.llm`` —
    owned by ``Orchestrator.__init__``.
    No ``__init__`` defined here.
    """

    async def on_heartbeat(self, clip_path: Path, level_db: float) -> None:
        state = self.state
        audio_source_label = self._classify_heartbeat_source(
            state.sonos_source, has_metadata=state.sonos_has_metadata,
        )
        if audio_source_label is None:
            return
        # Record the heartbeat level for the dead-air suppression gate.
        # Bounded by the deque's maxlen so no manual trim is needed.
        # See docs/features/llm-track-guess-suppress-on-dead-air/.
        state.recent_heartbeat_levels.append(level_db)
        # For airplay/streaming sources Sonos metadata is authoritative.
        # The cascade (Shazam, fingerprint, NEEDS_ID, predictions) must be
        # entirely inert — not even rate-limit budget should be spent. The
        # publish-time guards in _publish_shazam_match / _publish_needs_id /
        # _try_fingerprint_fallback are defence-in-depth; this early return is
        # the primary gate that stops the cascade and prevents streak
        # accumulation, prediction advances, and idle escalation.
        if state.sonos_source in ("airplay", "streaming"):
            log.debug(
                "heartbeat skipped: sonos_source=%s is Sonos-authoritative",
                state.sonos_source,
            )
            return
        # Music-level heartbeat means audio is back — retract any pending idle
        # before recognize starts so a slow Shazam call can't lose the race.
        if _is_music_level(level_db):
            self._retract_pending_idle_for_music(level_db)
        result = await self._run_recognizer(clip_path, audio_source_label)
        if result is None:
            return
        method = result.get("match_method")
        rid = result.get("release_id")
        log.info(
            "recognize: method=%s release_id=%s artist=%s title=%s",
            method, rid, result.get("artist"), result.get("title"),
        )
        if method != "shazam":
            await self._handle_non_shazam_heartbeat(
                method, clip_path, audio_source_label, level_db,
            )
            return
        # Record every Shazam result (even if gated/rejected) as LLM context
        # for ambiguous track-change decisions. Stored before gate checks so
        # the LLM can factor in low-confidence Shazam evidence.
        self._record_shazam_for_llm(state, result)
        # LLM-assisted reverse-lookup disambiguation — when the catalog
        # surfaced alternates within ~20 points of the winner, consult
        # the LLM with recent-history context to detect record flips
        # before relevance/publish. See
        # docs/features/llm-assisted-reverse-lookup/.
        await self._maybe_llm_disambiguate_reverse_lookup(result, state)
        if await self._llm_rejects_shazam_match(result, state):
            return
        await self._publish_shazam_match(
            result, audio_source_label, clip_path, level_db,
        )

    async def _handle_non_shazam_heartbeat(
        self, method, clip_path, audio_source_label: str, level_db: float,
    ) -> None:
        """Route a non-Shazam recognize outcome. F3 fingerprint-cascade
        fallback fires on a TRUE miss (method is None or "unmatched") with
        an album lock; everything else falls through to the unmatched path.

        Note: recognize_proto.recognize() returns match_method="unmatched"
        (the string) on a Shazam miss, never None. None is preserved for
        legacy callers. Both must gate the fingerprint fallback.
        """
        if method in (None, "unmatched"):
            if await self._try_fingerprint_fallback(clip_path, audio_source_label, level_db):
                return
        await self._handle_unmatched_heartbeat(audio_source_label, level_db)

    async def _publish_shazam_match(
        self, result: dict, audio_source_label: str,
        clip_path, level_db: float,
    ) -> None:
        """Commit a confirmed Shazam recognition: apply pin decision,
        transition state, and publish + record. Gated by the Shazam-only
        cross-heartbeat agreement check for results that don't resolve to
        a Discogs release.

        Shazam-hit-driven fingerprint promotion was removed — there is no
        value in seeding fp_refs from Shazam because Shazam will recognize
        those tracks reliably on replay. Promotion is pin-driven only
        (see ``_schedule_coverage_promotion``).
        """
        state = self.state
        rid = result.get("release_id")
        # Shazam matches that don't resolve to a release in the local Discogs
        # catalog gate on cross-heartbeat agreement before publishing. Gate
        # check runs BEFORE the streak/prediction resets so that a gated
        # hallucination (silence noise) doesn't prevent the system from
        # accumulating MAX_UNMATCHED_STREAK / NEEDS_ID_STREAK.
        if rid is None and not self._shazam_only_gate_passes(level_db, clip_path, result):
            return
        # Cascade-derived state must never overwrite Sonos-authoritative
        # metadata for streaming/AirPlay sources. Guard fires before any state
        # mutation so last_vinyl and streak state are preserved as-is.
        if state.sonos_source in ("airplay", "streaming"):
            log.info(
                "cascade publish suppressed: sonos_source=%s (shazam)",
                state.sonos_source,
            )
            return
        # Only reset streak / prediction state once we're committed to
        # publishing — i.e. after both gates above have passed.
        # Any confirmed match resets the unmatched streak.
        state.unmatched_streak = 0
        # Shazam confirmed a track — any active prediction is silently
        # superseded by the real recognition. (Caller continues to
        # overwrite state.last_vinyl below.)
        state.predicted_position = None
        # And any stale track-guess from a prior miss is no longer valid.
        state.pending_guess = None
        payload = to_now_playing_vinyl(result)
        payload["source"] = audio_source_label
        self._apply_pin_decision(payload, rid, result)
        state.last_vinyl = payload
        state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
        pos = payload.get("track_position")
        if pos:
            state.tracks_seen_since_audible_edge.add(pos)
        # Wall-clock stamp for the predicted-transition pin-backfill path;
        # stamped only here so it represents the *last Shazam-confirmed*
        # boundary, never a predicted-advance or fingerprint refresh.
        state.last_shazam_match_unix_ts = int(time.time())
        # Why: clearing the first-miss boundary here resets the gap so the
        # NEXT track's first miss restamps. See
        # docs/features/pin-position-ignores-predicted-advance-latency/.
        state.last_unmatched_after_match_unix_ts = None
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        if rid is not None:
            state.pending_shazam_only.clear()
        await self.bcast.publish(self._anchor_and_publish(payload))
        await _oc.history.record_play(payload)

    async def _lookup_fingerprint_hit(
        self, clip_path, locked_rid: int | None,
        locked_mbid: str | None = None,
    ) -> tuple[list[fingerprint.Hit], bytes | None]:
        """Read clip bytes and run the fingerprint match.

        ``locked_rid``:
        - ``int`` — confirmation scan against a single release (F3 path).
        - ``None`` — blind scan across all refs (F4 blind-discovery path).
          ``fingerprint.match`` dispatches to ``_fetch_ref_hash_rows_blind``
          in this case; passing ``None`` is safe and correct.

        Returns ``(hits, wav_bytes)`` where ``hits`` is the full sorted list
        of :class:`~nowplaying.vinyl.fingerprint.Hit` objects (may be empty)
        and ``wav_bytes`` is the raw WAV data (``None`` only if the file read
        itself failed).

        The caller (``_try_fingerprint_fallback``) applies the two-layer
        confidence gate (``MIN_FINGERPRINT_HITS_ANCHORED`` / ``MIN_FINGERPRINT_HITS_BLIND`` threshold + top-2 margin)
        rather than accepting the raw top hit verbatim.  Returning the full
        list lets the caller inspect the runner-up without a second DB query.

        Failure semantics (always returns a 2-tuple):
        - file read error  → ``([], None)``
        - match engine error → ``([], wav_bytes)``
        - empty result set → ``([], wav_bytes)``
        """
        try:
            wav_bytes = await asyncio.to_thread(_read_bytes, clip_path)
        except Exception as e:  # noqa: BLE001
            log.warning("fingerprint: read clip failed: %r", e)
            return [], None
        try:
            hits = await asyncio.to_thread(
                _cascade_match_dispatch, wav_bytes, locked_rid, locked_mbid,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("fingerprint: match raised %r", e)
            return [], wav_bytes
        if not hits:
            if locked_rid is not None:
                log.info("fingerprint: no match for release=%s", locked_rid)
            elif locked_mbid is not None:
                log.info("fingerprint: no match for mbid=%s", locked_mbid)
            else:
                log.info("fingerprint: blind scan — no match across all refs")
            return [], wav_bytes
        return hits, wav_bytes

    async def _try_fingerprint_fallback(
        self, clip_path, audio_source_label: str, level_db: float = 0.0,
    ) -> bool:
        """F3/F4 fingerprint-cascade fallback. Called from the Shazam-miss
        branch before _handle_unmatched_heartbeat.

        Returns True iff a fingerprint hit was found and published —
        in that case the caller should skip the unmatched path.

        Two mutually exclusive paths:

        F3 — Confirmation path (``last_vinyl`` set AND has ``release_id``):
            Scoped scan against the locked release only. On confidence-gated
            hit: build payload from locked album metadata. On miss or hit:
            run coverage-driven pin-promotion side effects
            (``_schedule_coverage_promotion``). On miss: also run
            track-guess side effects.

        F4 — Blind path (``last_vinyl`` is None OR has no ``release_id``):
            Blind scan across ALL refs in ``fp_refs``. Applies the same two
            confidence gates as F3. On hit: build payload from
            ``discogs.sqlite`` via ``_build_blind_fingerprint_payload``.
            Does NOT call ``_schedule_coverage_promotion`` (requires a
            locked anchor) or ``_compute_track_guess`` (requires a locked
            tracklist). If the catalog lookup returns None, falls through as
            a miss.

        Gate: ``fingerprint_enabled`` must be True for either path.

        ``level_db`` is forwarded to ``_try_confirmation_fingerprint`` for the
        silence-floor gate inside ``_schedule_coverage_promotion``.
        """
        if not self.fingerprint_enabled:
            return False
        state = self.state

        # ── Route: confirmation vs. blind ───────────────────────────────
        last_vinyl = state.last_vinyl or {}
        locked_rid = last_vinyl.get("release_id")
        locked_mbid = last_vinyl.get("release_mbid")
        is_blind = (
            state.last_vinyl is None
            or (locked_rid is None and not locked_mbid)
        )

        if is_blind:
            return await self._try_blind_fingerprint(clip_path, audio_source_label)
        return await self._try_confirmation_fingerprint(
            clip_path, audio_source_label, locked_rid, level_db,
            locked_mbid=locked_mbid if locked_rid is None else None,
        )

    async def _confirmation_reject_with_side_effects(
        self, state, wav_bytes, level_db: float,
    ) -> bool:
        """Shared rejection path for no-hit / below-threshold / margin failures
        in the F3 confirmation scan.

        Even on rejection, we still want to offer the clip to the
        coverage-driven promotion helper (if the user has an active track pin)
        and stash a heuristic track-guess for the next publish to attach. The
        next publish (NEEDS_ID, predicted advance, or predicted re-publish —
        all route through _anchor_and_publish) attaches it to the payload. The
        UI (Feature D) uses it to surface a confirmation prompt.
        """
        if wav_bytes is not None:
            await self._schedule_coverage_promotion(wav_bytes, level_db)
        guess_obj = await self._compute_track_guess(state)
        if guess_obj is not None:
            state.pending_guess = guess_obj
        return False

    async def _confirmation_position_change_publish(
        self, state, audio_source_label: str, top,
    ) -> bool:
        """Handle a confirmed F3 hit where the new position differs from the
        anchor position — the user physically moved the needle.

        Clears the stale anchor, builds a fresh catalog payload, and publishes.
        Returns True on successful publish, False when the release is absent
        from the local catalog (treat as miss). Must only be called when an
        anchor mismatch has already been detected by the caller.
        """
        # Cascade-derived state must never overwrite Sonos-authoritative
        # metadata for streaming/AirPlay sources.
        if state.sonos_source in ("airplay", "streaming"):
            log.info(
                "cascade publish suppressed: sonos_source=%s (fingerprint position change)",
                state.sonos_source,
            )
            return True
        # Clear the stale anchor so predicted-advance can't use it.
        state.fingerprint_anchor = None
        # Build a complete payload from the catalog (not from the stale
        # last_vinyl) so title, artist, art, and tracklist all reflect the
        # new track.  If the release isn't in the local catalog, treat as a
        # miss rather than publishing an inconsistent payload.
        payload = _build_blind_fingerprint_payload(top, audio_source_label)
        if payload is None:
            log.info(
                "fingerprint: position change to release=%s pos=%s but release not in discogs catalog — falling through",
                top.release_id, top.track_position,
            )
            return False
        state.unmatched_streak = 0
        state.predicted_position = None
        state.pending_guess = None
        state.last_vinyl = payload
        state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        # Re-anchor on the new position if the hit is strong enough.
        self._set_fingerprint_anchor(state, top, payload)
        await self.bcast.publish(self._anchor_and_publish(payload))
        try:
            await _oc.history.record_play(payload)
        except Exception as e:  # noqa: BLE001
            log.warning("history.record_play (fp position change) failed: %r", e)
        return True

    async def _confirmation_hit_publish(
        self, state, audio_source_label: str, top, wav_bytes, level_db: float,
    ) -> bool:
        """Commit a confidence-gated F3 confirmation hit: update state, refresh
        anchor, run coverage promotion, and publish.

        Called after both confidence gates pass and no anchor mismatch is
        detected. Returns True always (caller uses the return value to skip the
        unmatched path).
        """
        # ── Cascade-derived state must never overwrite Sonos-authoritative ─
        # metadata for streaming/AirPlay sources.
        if state.sonos_source in ("airplay", "streaming"):
            log.info(
                "cascade publish suppressed: sonos_source=%s (fingerprint)",
                state.sonos_source,
            )
            return True
        payload = _oc._build_fingerprint_payload(
            state.last_vinyl, top, audio_source_label,
        )
        state.unmatched_streak = 0
        state.predicted_position = None
        state.pending_guess = None
        state.last_vinyl = payload
        state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        # ── Refresh the anchor on every strong confirmation hit ─────────────
        # This keeps the anchor TTL current even when the position hasn't
        # changed, preventing predicted-advance from firing during coverage
        # gaps later in the track.
        self._set_fingerprint_anchor(state, top, payload)
        # ── Coverage-driven promotion on HIT ────────────────────────────
        # Even when fingerprint hits, run the spatial coverage check — the
        # existing ref that matched may be far from the current
        # track_position_s, and adjacent positions still need coverage. This
        # is the key Rule B change: promotion is coverage-driven, not
        # outcome-driven.
        if wav_bytes is not None:
            await self._schedule_coverage_promotion(wav_bytes, level_db)
        await self.bcast.publish(self._anchor_and_publish(payload))
        try:
            await _oc.history.record_play(payload)
        except Exception as e:  # noqa: BLE001
            log.warning("history.record_play (fingerprint) failed: %r", e)
        return True

    async def _try_confirmation_fingerprint(
        self, clip_path, audio_source_label: str, locked_rid: int | None,
        level_db: float = 0.0,
        *,
        locked_mbid: str | None = None,
    ) -> bool:
        """F3 confirmation fingerprint scan — album lock is present.

        Scans only refs for ``locked_rid``. On miss OR hit: runs
        coverage-driven pin-promotion (``_schedule_coverage_promotion``) so
        that every heartbeat where a pin is active fills coverage gaps,
        not just misses. On miss: also runs track-guess side effects.

        ``level_db`` is passed to ``_schedule_coverage_promotion`` for the
        silence-floor gate (no garbage refs from between-track gaps).
        """
        state = self.state
        hits, wav_bytes = await self._lookup_fingerprint_hit(
            clip_path, locked_rid, locked_mbid=locked_mbid,
        )
        # Record the top fingerprint hit (even sub-threshold) for LLM context.
        # The LLM uses recent_fp_hits to judge ambiguous track-change decisions.
        self._record_fp_hit_for_llm(state, hits)

        if not hits:
            return await self._confirmation_reject_with_side_effects(state, wav_bytes, level_db)

        top = hits[0]
        # ── Confidence gate 1: minimum-hits threshold ───────────────────
        if top.hits < MIN_FINGERPRINT_HITS_ANCHORED:
            log.info(
                "fingerprint: below threshold (hits=%d, min=%d) — treating as no match",
                top.hits, MIN_FINGERPRINT_HITS_ANCHORED,
            )
            return await self._confirmation_reject_with_side_effects(state, wav_bytes, level_db)
        # ── Confidence gate 2: top-2 margin requirement ─────────────────
        if len(hits) >= 2 and top.hits < 2 * hits[1].hits:
            log.info(
                "fingerprint: insufficient margin (top=%d runner_up=%d) — treating as no match",
                top.hits, hits[1].hits,
            )
            return await self._confirmation_reject_with_side_effects(state, wav_bytes, level_db)
        log.info(
            "fingerprint: matched release=%s pos=%s hits=%d",
            top.release_id, top.track_position, top.hits,
        )
        # ── Position-change guard: detect needle-move to a different track ─
        # When an anchor is active, compare the new hit's position against the
        # anchor's position.  If they differ, the user has physically moved the
        # needle to a new track and the anchor must be released; we build a
        # FRESH payload from the catalog rather than overlaying on stale
        # last_vinyl (which would produce a "Frankenstein" payload with the old
        # title and the new track_position).
        #
        # When no anchor is set, fall through to the normal overlay path.
        # _build_fingerprint_payload looks up the new title from the locked
        # tracklist, so it is consistent even without an anchor.  For cases
        # where last_vinyl was built by the blind path (tracklist uses the
        # "position" key instead of "track_position"), _build_fingerprint_payload
        # also checks "position" so the title lookup is correct.
        #
        # Reviewer finding: also check last_vinyl.track_position to catch
        # the no-anchor case where prior hits were below the strong threshold.
        # We do this by preferring anchor_pos when set, then falling back to
        # last_vinyl.track_position — but only if last_vinyl was NOT built by the
        # overlay path itself (i.e., last_vinyl was set by the blind path, making
        # its tracklist use "position" keys, which is detectable by the key name).
        # To avoid complicating this logic, we use a conservative approach:
        # only trigger the guard on an anchor mismatch; the overlay path
        # correctly handles intra-album position advances.
        anchor = state.fingerprint_anchor
        anchor_pos = anchor.get("track_position") if isinstance(anchor, dict) else None
        if anchor_pos is not None and top.track_position != anchor_pos:
            log.info(
                "fingerprint: position changed %s → %s — releasing anchor, building fresh payload",
                anchor_pos, top.track_position,
            )
            return await self._confirmation_position_change_publish(
                state, audio_source_label, top,
            )
        return await self._confirmation_hit_publish(
            state, audio_source_label, top, wav_bytes, level_db,
        )

    async def _blind_hit_publish(
        self, state, audio_source_label: str, top,
    ) -> bool:
        """Commit a confidence-gated F4 blind hit: guard against Sonos-
        authoritative sources, build catalog payload, clear stale anchor,
        set new anchor on strong hits, and publish.

        Returns True on successful publish, False when the release is absent
        from the local discogs catalog (treat as miss). Must only be called
        after both confidence gates have passed.
        """
        # Cascade-derived state must never overwrite Sonos-authoritative
        # metadata for streaming/AirPlay sources.
        if state.sonos_source in ("airplay", "streaming"):
            log.info(
                "cascade publish suppressed: sonos_source=%s (blind fingerprint)",
                state.sonos_source,
            )
            return True
        payload = _build_blind_fingerprint_payload(top, audio_source_label)
        if payload is None:
            # Release not in local discogs catalog — treat as no match.
            log.info(
                "fingerprint: blind matched release=%s not in discogs catalog — falling through",
                top.release_id,
            )
            return False
        state.unmatched_streak = 0
        state.predicted_position = None
        state.pending_guess = None
        state.last_vinyl = payload
        state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        # ── Position-change guard: clear stale anchor before setting new one ─
        # If a fingerprint anchor is active on a different position (or release),
        # explicitly clear it so predicted-advance can't use the stale anchor
        # during the gap between this publish and the next strong hit.
        # _set_fingerprint_anchor below will re-anchor on the new position
        # if hits clear the strong threshold.
        existing_anchor = state.fingerprint_anchor
        if isinstance(existing_anchor, dict):
            if (
                existing_anchor.get("track_position") != top.track_position
                or existing_anchor.get("release_id") != top.release_id
            ):
                log.info(
                    "fingerprint: blind position/release changed %s/%s → %s/%s — releasing stale anchor",
                    existing_anchor.get("release_id"), existing_anchor.get("track_position"),
                    top.release_id, top.track_position,
                )
                state.fingerprint_anchor = None
        # ── Fingerprint anchor: block predicted-advance on strong hits ───
        # A blind match above the strong-confidence threshold is treated as
        # ground truth equivalent to a user pin. Set (or refresh) the
        # fingerprint anchor so that _handle_unmatched_music_level won't
        # flip to predicted-advance during transient coverage-hole misses.
        # The anchor survives miss heartbeats; TTL = track duration handles
        # natural expiry. Weaker hits (MIN ≤ hits < threshold) publish
        # normally but do not set an anchor — they're sufficient to identify
        # the track but not authoritative enough to pin predicted-advance.
        self._set_fingerprint_anchor(state, top, payload)
        await self.bcast.publish(self._anchor_and_publish(payload))
        try:
            await _oc.history.record_play(payload)
        except Exception as e:  # noqa: BLE001
            log.warning("history.record_play (blind fingerprint) failed: %r", e)
        return True

    async def _try_blind_fingerprint(
        self, clip_path, audio_source_label: str,
    ) -> bool:
        """F4 blind fingerprint scan — no album lock.

        Scans ALL refs in ``fp_refs`` without a release filter. Applies
        the same two confidence gates as the confirmation path. On a
        confidence-gated hit, builds the payload via
        ``_build_blind_fingerprint_payload`` (which fetches release metadata
        from ``discogs.sqlite``).

        Deliberately does NOT call ``_schedule_coverage_promotion`` (requires
        a locked album anchor — no lock exists here) or ``_compute_track_guess``
        (requires a locked tracklist). The blind rejection path therefore has no
        side effects beyond returning False.

        Read-only: a blind match never auto-promotes refs to ``fp_refs``.
        Promotion stays pin-driven only.
        """
        log.info("fingerprint: blind scan — no lock, scanning all refs")
        state = self.state
        hits, _wav_bytes = await self._lookup_fingerprint_hit(clip_path, None)

        if not hits:
            return False

        top = hits[0]
        # ── Confidence gate 1: minimum-hits threshold ───────────────────
        if top.hits < MIN_FINGERPRINT_HITS_BLIND:
            log.info(
                "fingerprint: blind below threshold (hits=%d, min=%d) — treating as no match",
                top.hits, MIN_FINGERPRINT_HITS_BLIND,
            )
            return False
        # ── Confidence gate 2: top-2 margin requirement ─────────────────
        if len(hits) >= 2 and top.hits < 2 * hits[1].hits:
            log.info(
                "fingerprint: blind insufficient margin (top=%d runner_up=%d) — treating as no match",
                top.hits, hits[1].hits,
            )
            return False
        log.info(
            "fingerprint: blind matched release=%s pos=%s hits=%d",
            top.release_id, top.track_position, top.hits,
        )
        return await self._blind_hit_publish(state, audio_source_label, top)

    def _set_fingerprint_anchor(self, state, top, payload: dict) -> None:
        """Set (or refresh) the fingerprint anchor when a hit clears the
        strong-confidence threshold.

        Called by both the blind path and the confirmation path (including
        the position-change transition) to keep anchor logic DRY.

        Sets ``state.fingerprint_anchor`` with the current monotonic
        timestamp so the anchor TTL resets on each strong confirmation.
        A no-op if ``top.hits`` is below the strong threshold.

        ``payload`` is used only to resolve track duration from the
        tracklist.  The tracklist may use either ``"position"`` (blind
        payload built by ``_build_blind_fingerprint_payload``) or
        ``"track_position"`` (confirmation-overlay payload) as the key,
        so both are checked.
        """
        if top.hits < MIN_FINGERPRINT_HITS_ANCHORED * STRONG_FINGERPRINT_ANCHOR_MULTIPLIER:
            return
        now_mono = asyncio.get_running_loop().time()
        duration_s: int | None = None
        for tr in (payload.get("tracklist") or []):
            # Blind payloads use "position"; confirmation-overlay payloads
            # use "track_position".  Check both so this helper works for
            # every call site.
            tr_pos = tr.get("position") or tr.get("track_position")
            if tr_pos == top.track_position:
                duration_s = tr.get("duration_seconds")
                break
        state.fingerprint_anchor = {
            "release_id": top.release_id,
            "mbid": top.mbid,
            "track_position": top.track_position,
            "monotonic_ts": now_mono,
            "hits": top.hits,
            "duration_seconds": duration_s,
            # Anchor-driven coverage promotion uses this + (now_mono -
            # monotonic_ts) to compute current track_position_s without
            # depending on the wall-clock track_started_at (which has
            # its own backdate accuracy issue tracked separately).
            "last_matched_ref_position_s": top.track_position_s,
        }
        log.info(
            "fingerprint: anchor set release=%s pos=%s hits=%d duration=%s",
            top.release_id, top.track_position, top.hits, duration_s,
        )

    async def _schedule_coverage_promotion(  # skylos: ignore SKY-Q301 SKY-C304 — Why: CC 11 / 88 lines come from two independent drive paths (pin vs anchor) each with early-return guards; splitting to sub-helpers would require threading 6+ local variables through call boundaries with no net clarity gain
        self, wav_bytes: bytes, level_db: float,
    ) -> None:
        """Coverage-driven promotion — fires on every heartbeat where
        EITHER a pin OR a fingerprint anchor is active AND audio is above
        the silence floor, regardless of whether the fingerprint scan hit
        or missed.

        Checks spatially whether a ref already exists near the current
        ``track_position_s``. Only schedules ``maybe_promote`` when a
        coverage gap is found.

        Pin path: ``track_position_s`` is elapsed monotonic time since
        the pin was set (user mental model: "start fingerprinting from
        here"). Anchor path (fallback when no pin): ``track_position_s``
        is the last matched ref's position_s plus monotonic elapsed
        since the anchor refresh — no wall-clock dependency.

        Side effect only; does not change the cascade's recognition verdict.
        """
        if not self.fingerprint_enabled:
            return
        if not _is_music_level(level_db):
            # Silence floor gate: no garbage refs from between-track gaps.
            return
        now_mono = asyncio.get_running_loop().time()
        pin = self.state.user_track_pin
        # Resolve drive: pin takes precedence over anchor when both
        # are present (preserves PR #183 behaviour exactly).
        drive: str
        release_id: int | None
        mbid: str | None = None
        track_position: str | None
        duration_s: float | None
        elapsed: float
        if pin is not None:
            if _pin_ttl_expired(pin, now_mono):
                return
            release_id = pin["release_id"]
            track_position = pin["track_position"]
            duration_s = pin.get("duration_seconds")
            # Pin's initial_track_position_s anchors the position math to
            # where the audio actually was at pin time (estimated from
            # the most recent audible-edge or fingerprint anchor). Without
            # this, every pin started at track_position_s=0 and tagged refs
            # at wrong positions when the user clicked mid-track.
            initial_pos = float(pin.get("initial_track_position_s") or 0.0)
            elapsed = initial_pos + (now_mono - pin["monotonic_ts"])
            drive = "pin"
        else:
            anchor = self.state.fingerprint_anchor
            if anchor is None or _fingerprint_anchor_ttl_expired(anchor, now_mono):
                return
            last_pos = anchor.get("last_matched_ref_position_s")
            if last_pos is None:
                # Backward-compat against mid-deploy anchors written by
                # the pre-this-feature build; silently skip rather than
                # crash. Refreshed on the next strong fingerprint match.
                return
            release_id = anchor.get("release_id")
            mbid = anchor.get("mbid")
            track_position = anchor["track_position"]
            duration_s = anchor.get("duration_seconds")
            elapsed = float(last_pos) + (now_mono - anchor["monotonic_ts"])
            drive = "anchor"
        if not track_position:
            return
        if release_id is None and mbid is None:
            return
        # MBID-keyed promotion bypasses the Discogs-cohort gates (cross-cohort
        # guard + spacing/cap are release_id-keyed in vinyl.promotion). The
        # UNIQUE constraint on discovery.fingerprint.fp_refs is the only gate
        # against duplicate writes; that's sufficient given today's anchor-only
        # discovered-promotion flow (no user pin on discovered releases yet).
        if release_id is None:
            log.info(
                "promotion: coverage-gap (%s) mbid=%s pos=%s track_position_s=%.1f duration=%s — scheduling (discovered)",
                drive, mbid, track_position, elapsed, duration_s,
            )
            asyncio.create_task(
                asyncio.to_thread(
                    discovery_fingerprint.add_ref,
                    mbid, track_position, elapsed, wav_bytes,
                ),
            )
            return
        # Spatial coverage check — off the event loop (DB read).
        gap_found = await asyncio.to_thread(
            promotion.should_promote_for_coverage,
            release_id,
            track_position,
            elapsed,
            duration_s=duration_s,
        )
        if not gap_found:
            return
        log.info(
            "promotion: coverage-gap (%s) release=%s pos=%s track_position_s=%.1f duration=%s — scheduling",
            drive, release_id, track_position, elapsed, duration_s,
        )
        asyncio.create_task(
            promotion.maybe_promote(
                release_id=release_id,
                track_position=track_position,
                track_position_s=elapsed,
                wav_bytes=wav_bytes,
                duration_s=duration_s,
            ),
        )

    def _classify_heartbeat_source(
        self, src: str | None, *, has_metadata: bool,
    ) -> str | None:
        """Return the audio-source label to attach to a heartbeat recognition,
        or None when this heartbeat should be ignored. Vinyl always recognizes;
        AirPlay recognizes only when Sonos has no track metadata (system audio).
        """
        if src == "vinyl":
            return "vinyl"
        if src == "airplay" and not has_metadata:
            return "airplay"
        log.info(
            "ignoring heartbeat; sonos source is %s (has_metadata=%s)",
            src, has_metadata,
        )
        return None

    def _retract_pending_idle_for_music(self, level_db: float) -> None:
        """Cancel a pending idle timer and clear sticky-idle when capture
        observes music-level audio. Two independent actions so an in-flight
        idle is cancelled even if sticky-idle isn't set, and vice versa.
        """
        state = self.state
        if state.idle_task is not None and not state.idle_task.done():
            log.info("idle retracted: heartbeat at level_db=%.1f", level_db)
            state.idle_task.cancel()
            state.idle_task = None
            state.unmatched_streak = 0
        # Defensive: if a Sonos "back to playing" event is delayed but capture
        # already hears music, clear sticky-idle so the next publish can lift
        # the kiosk off the clock.
        if state.idled_source is not None:
            log.info(
                "streaming idle cleared: heartbeat at level_db=%.1f "
                "while idled_source=%s",
                level_db, state.idled_source,
            )
            state.idled_source = None
            state.idled_title = None

    async def _run_recognizer(
        self, clip_path: Path, audio_source_label: str,
    ) -> dict | None:
        """Run a recognition pass for this heartbeat. Returns the recognizer
        result dict or None when the recognizer raised (logged + swallowed)."""
        state = self.state
        try:
            preferred = (state.last_vinyl or {}).get("release_id")
            return await recognize_proto.recognize(
                clip_path,
                preferred_release_id=int(preferred) if preferred is not None else None,
                source=audio_source_label,
            )
        except Exception as e:
            log.exception("recognize failed: %r", e)
            return None
