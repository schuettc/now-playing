"""SonosHandlersMixin — Sonos and capture event handlers.

Contains: on_sonos_event, _handle_sticky_idle, _apply_source_transition,
_maybe_cancel_idle_on_resume, _reconcile_capture_emit,
_recover_unmatched_from_cache, _reset_for_non_idle_source,
_maybe_arm_streaming_idle, on_capture_state, _handle_capture_silent,
_handle_capture_audible.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import recognize_proto

from nowplaying import history
from nowplaying.vinyl.runtime import signal_capture
from nowplaying.orchestrator.payload import sonos_to_payload
from nowplaying.orchestrator.streaming_idle import (
    STREAMING_IDLE_DELAY_S,
    VINYL_IDLE_DELAY_S,
    _evaluate_sticky_idle,
    _should_arm_streaming_idle,
    _should_cancel_streaming_idle_on_resume,
    _should_pause_capture,
)

log = logging.getLogger("nowplaying.main")


class SonosHandlersMixin:
    """Sonos and capture event handlers.

    All state is accessed via ``self.state``, ``self.bcast``, and
    ``self.sonos_coord`` — owned by ``Orchestrator.__init__``.
    No ``__init__`` defined here.
    """

    async def on_sonos_event(self, ev: dict) -> None:
        state = self.state
        bcast = self.bcast
        try:
            payload = sonos_to_payload(ev)
        except Exception as e:
            log.exception("sonos translate failed: %r", e)
            return

        if self._handle_sticky_idle(payload):
            return

        self._apply_source_transition(ev, payload)
        self._maybe_cancel_idle_on_resume(payload)
        self._reconcile_capture_emit()
        payload = self._recover_unmatched_from_cache(payload)
        self._reset_for_non_idle_source(payload)

        log.info(
            "sonos: state=%s source=%s artist=%s title=%s",
            payload["state"], payload["source"],
            payload.get("artist"), payload.get("title"),
        )
        # AirPlay / streaming enrichment: if the song happens to be on
        # a record in the user's Discogs collection, patch release_id +
        # canonical art. AirPlay also gets the album tracklist; streaming
        # skips it because it has a real Sonos queue (see below).
        payload = self._enrich_sonos_with_discogs(payload)
        # Non-matched non-vinyl tracks: if an art override exists for
        # this (artist, album), rewrite art_url to /art-by-name so the
        # override is served. Lives OUTSIDE _enrich_sonos_with_discogs
        # because that function short-circuits when there's no Discogs
        # match — the exact case this rewrite targets.
        payload = self._rewrite_art_url_for_overrides(payload)
        # Sonos-native streaming sources also get the upcoming queue
        # attached as `queue: [...]` for the kiosk's Up Next panel.
        payload = await self._enrich_with_queue(payload)
        # Keep last_vinyl (the universal "current track" cache) in sync
        # for airplay + streaming too, so the repoll loop can detect
        # "track changed since last publish."
        if payload.get("source") in ("airplay", "streaming") and payload.get("title"):
            state.last_vinyl = payload
        self._maybe_arm_streaming_idle(payload)
        await bcast.publish(self._anchor_and_publish(payload))
        await history.record_play(payload)

    def _handle_sticky_idle(self, payload: dict) -> bool:
        """Evaluate sticky-idle short-circuit. Returns True if the event
        should be dropped, False to continue processing. Side-effect:
        clears idled_source/title on "clear" verdict.
        """
        state = self.state
        sticky = _evaluate_sticky_idle(
            state.idled_source, state.idled_title,
            payload.get("source"), payload.get("state"),
            payload.get("title"),
        )
        if sticky == "short_circuit":
            log.debug(
                "streaming idle sticky: dropping repoll event "
                "(source=%s state=%s)",
                payload.get("source"), payload.get("state"),
            )
            return True
        if sticky == "clear":
            log.info(
                "streaming idle cleared: was=%s now=(source=%s state=%s title=%r)",
                state.idled_source, payload.get("source"),
                payload.get("state"), payload.get("title"),
            )
            state.idled_source = None
            state.idled_title = None
        return False

    def _apply_source_transition(self, ev: dict, payload: dict) -> None:
        """Update state.sonos_source/state/has_metadata from the incoming
        event, and on any source change clear cross-heartbeat agreement
        and cancel any in-flight idle from the previous source.
        """
        state = self.state
        previous_source = state.sonos_source
        state.sonos_source = ev.get("source", "unknown")
        state.sonos_state = payload["state"]
        state.sonos_has_metadata = bool(payload.get("title"))
        if previous_source == state.sonos_source:
            return
        state.pending_shazam_only.clear()
        # Clear any stale track-guess so it can't be attached to the first
        # publish after the source flip (e.g., vinyl guess leaked into AirPlay).
        state.pending_guess = None
        if state.idle_task is not None and not state.idle_task.done():
            log.info(
                "idle retracted: source change %s -> %s",
                previous_source, state.sonos_source,
            )
            state.idle_task.cancel()
            state.idle_task = None

    def _maybe_cancel_idle_on_resume(self, payload: dict) -> None:
        """Streaming / AirPlay resume → cancel any pending pause-idle.
        Placed before the publish path so a paused→playing edge can't
        publish a stale STOPPED if the timer was about to fire.
        """
        state = self.state
        if not _should_cancel_streaming_idle_on_resume(
            payload_source=payload.get("source"),
            payload_state=payload.get("state"),
            idle_task_alive=state.idle_task is not None and not state.idle_task.done(),
        ):
            return
        log.info(
            "streaming idle retracted: state=PLAYING source=%s",
            payload["source"],
        )
        state.idle_task.cancel()
        state.idle_task = None

    def _reconcile_capture_emit(self) -> None:
        """Reconcile capture emit state on every event — covers source
        changes AND metadata appearing/disappearing during an AirPlay
        session. Only update local state when signal delivery succeeded.
        """
        state = self.state
        desired_paused = _should_pause_capture(state)
        if desired_paused == state.capture_emit_paused:
            return
        sig_to_send = signal.SIGHUP if desired_paused else signal.SIGCONT
        if signal_capture(sig_to_send):
            state.capture_emit_paused = desired_paused

    def _recover_unmatched_from_cache(self, payload: dict) -> dict:
        """When a Sonos event arrives for a UFO202-listened source with
        no title, prefer the last cached recognition for that same source
        if we have one. Prevents flickering to "Unknown" during volume
        changes or transport-only events that lack metadata. Returns the
        (possibly replaced) payload.
        """
        state = self.state
        if payload["source"] not in ("vinyl", "airplay") or payload["title"]:
            return payload
        cached = state.last_vinyl
        if cached is not None and cached.get("source") == payload["source"]:
            return {**cached, "ts": payload["ts"], "state": payload["state"]}
        payload["match_method"] = "unmatched"
        return payload

    def _reset_for_non_idle_source(self, payload: dict) -> None:
        """Source flipped to a no-idle-handling source (TV, line-in, etc.).
        Clear caches, release the user pin, cancel any idle task.
        """
        state = self.state
        if payload["source"] in ("vinyl", "airplay", "streaming"):
            return
        state.last_vinyl = None
        state.last_vinyl_confidence_set_at = None
        state.last_shazam_match_unix_ts = None
        state.last_pin_unix_ts = None
        state.last_unmatched_after_match_unix_ts = None
        state.pending_shazam_only.clear()
        state.recent_fp_hits.clear()
        state.last_shazam_gated = None
        state.recent_audible_edges.clear()
        state.tracks_seen_since_audible_edge.clear()
        state.audible_up_at_mono = None
        state.recent_heartbeat_levels.clear()
        if state.user_track_pin is not None:
            log.info("pin released: reason=source_flip new_source=%s", payload["source"])
        state.user_track_pin = None
        state.pin_different_track_streak = 0
        state.fingerprint_anchor = None
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None

    def _maybe_arm_streaming_idle(self, payload: dict) -> None:
        """Streaming / AirPlay pause-driven idle: arm the 10-minute
        timer when conditions match (see _should_arm_streaming_idle).
        """
        state = self.state
        if not _should_arm_streaming_idle(
            payload_source=payload.get("source"),
            payload_state=payload.get("state"),
            idled_source=state.idled_source,
            idle_task_alive=state.idle_task is not None and not state.idle_task.done(),
        ):
            return
        log.info(
            "streaming idle armed: source=%s state=%s delay=%ds",
            payload["source"], payload["state"],
            STREAMING_IDLE_DELAY_S,
        )
        state.idle_task = asyncio.create_task(
            self._idle_after_delay(
                STREAMING_IDLE_DELAY_S, payload["source"],
            ),
        )

    async def on_capture_state(self, s: str) -> None:
        state = self.state
        # Common guards: capture state events only apply to UFO202-listened
        # sources (vinyl + AirPlay-without-Sonos-metadata). Streaming with
        # Sonos metadata has its own publish path.
        if (
            state.sonos_source not in ("vinyl", "airplay")
            or state.capture_emit_paused
        ):
            return
        if s == "silent":
            self._handle_capture_silent()
            return
        if s == "audible":
            await self._handle_capture_audible()

    def _handle_capture_silent(self) -> None:
        """Sustained silence → arm idle timer that clears the kiosk.
        Also clear any active prediction: sustained silent means side
        flip or LP end, and predictions must not carry across — the next
        audible on a new side needs a Shazam confirm before predictions
        can resume.
        """
        state = self.state
        self._record_audible_edge_for_llm(state, "silent")
        state.predicted_position = None
        if state.idle_task is not None and not state.idle_task.done():
            return
        # Tag with current source (vinyl or airplay-no-metadata) so
        # the STOPPED publish stays accurate.
        state.idle_task = asyncio.create_task(
            self._idle_after_delay(VINYL_IDLE_DELAY_S, state.sonos_source),
        )

    async def _handle_capture_audible(self) -> None:
        """Audible IPC event = "new record / side started". Clear per-side
        recognition state, then either advance the predicted tracklist
        (album-locked) or publish a spinner transition (no lock).
        """
        state = self.state
        bcast = self.bcast
        self._record_audible_edge_for_llm(state, "audible")
        state.pending_shazam_only.clear()
        state.track_started_at = None
        log.info("audible: cleared per-side recognition state")
        audio_source_label = (
            "vinyl" if state.sonos_source == "vinyl" else "airplay"
        )
        if state.last_vinyl is not None:
            # Cancel any in-flight idle timer FIRST so it doesn't
            # fire 45s later and wipe the lock out from under the
            # prediction we're about to publish (PR #109 review
            # blocker).
            if state.idle_task is not None and not state.idle_task.done():
                state.idle_task.cancel()
                state.idle_task = None
            await self._try_advance_prediction(
                state, audio_source_label, bcast,
            )
            # End-of-side or catalog miss — keep current display.
            return
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        # Reset the unmatched-streak counter when we transition to the
        # spinner. The streak is what gates the NEEDS_ID escalation; if
        # we leave a high streak in place after publishing the spinner,
        # subsequent unmatched heartbeats land in the "still in NEEDS_ID
        # — don't republish" branch and the kiosk gets stuck on the
        # spinner indefinitely.
        state.unmatched_streak = 0
        payload = {
            "ts": recognize_proto.now_iso(),
            "state": "PLAYING",
            "source": audio_source_label,
            "title": None,
            "match_method": "unmatched",
        }
        log.info(
            "audible: publishing VinylIdentifying transition (source=%s)",
            audio_source_label,
        )
        await bcast.publish(payload)
