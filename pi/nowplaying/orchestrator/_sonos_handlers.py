"""SonosHandlersMixin — Sonos and capture event handlers.

Contains: adopt_sonos_coord, on_sonos_event, _process_sonos_event, _handle_sticky_idle, _apply_source_transition,
_maybe_cancel_idle_on_resume, _reconcile_capture_emit,
_recover_unmatched_from_cache, _reset_for_non_idle_source,
_maybe_arm_streaming_idle, on_capture_state, _handle_capture_silent,
_handle_capture_audible, _follow_timeline_on_sound, _retime_current_track.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

import recognize_proto

from nowplaying import history
from nowplaying.orchestrator import side_timeline
from nowplaying.orchestrator._prediction_advance import iso_z
from nowplaying.orchestrator.payload import sonos_to_payload
from nowplaying.orchestrator.streaming_idle import (
    STREAMING_IDLE_DELAY_S,
    VINYL_IDLE_DELAY_S,
    _evaluate_sticky_idle,
    _should_arm_streaming_idle,
    _should_cancel_streaming_idle_on_resume,
    _should_pause_capture,
)
from nowplaying.vinyl.runtime import signal_capture

log = logging.getLogger("nowplaying.main")

# The capture daemon reports `silent` once the quiet has lasted this long,
# so the quiet began this much before the edge.
SILENT_EDGE_DELAY_S = 5.0


class SonosHandlersMixin:
    """Sonos and capture event handlers.

    All state is accessed via ``self.state``, ``self.bcast``, and
    ``self.sonos_coord`` — owned by ``Orchestrator.__init__``.
    No ``__init__`` defined here.
    """

    def adopt_sonos_coord(self, coord) -> None:
        """Take the coordinator the listener is subscribed to, so the
        repoll and queue polls follow the zone when it is re-discovered.
        """
        self.sonos_coord = coord

    async def on_sonos_event(self, ev: dict) -> None:
        """Handle one Sonos event (a NOTIFY, an enrich-republish or a
        repoll tick). Each caller runs in its own task, so events are
        taken one at a time in arrival order: the queue poll below awaits,
        and an older event finishing late must not publish over a newer one.
        """
        async with self.state.sonos_event_lock:
            await self._process_sonos_event(ev)

    async def _process_sonos_event(self, ev: dict) -> None:
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
        # album metadata. Art is NOT patched — the streaming service
        # already knows exactly which cover is playing. AirPlay also gets
        # the album tracklist; streaming skips it because it has a real
        # Sonos queue (see below).
        payload = self._enrich_sonos_with_discogs(payload)
        # A release pick beats service art; a name-only pick applies only
        # without release identity. Keep this outside Discogs enrichment so
        # unmatched streams can still use their own manual picks.
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
        # The broadcaster returns False when it suppressed the publish as
        # content-identical. That verdict is proof this is the same
        # continuous play, not a new one — so record it extend-only (extend
        # ended_at, never insert). Without this, a duplicate airplay NOTIFY
        # (~120 s cadence, past history's 60 s coalesce window) inserted a
        # fresh row every time; see history.record_play.
        published = await bcast.publish(self._anchor_and_publish(payload))
        await history.record_play(payload, extend_only=not published)

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
        event, and on any source change end the previous source's session
        (lock, pin, anchor, prediction) and cancel its in-flight idle.
        """
        state = self.state
        previous_source = state.sonos_source
        state.sonos_source = ev.get("source", "unknown")
        state.sonos_state = payload["state"]
        state.sonos_has_metadata = bool(payload.get("title"))
        if previous_source == state.sonos_source:
            return
        if state.user_track_pin is not None:
            log.info(
                "pin released: reason=source_change %s -> %s",
                previous_source, state.sonos_source,
            )
        state.reset_vinyl_session()
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
        """Reconcile capture emit state on every event, so a source change
        pauses or resumes capture and a failed signal is retried on the
        next event. Only update local state when signal delivery succeeded.
        """
        state = self.state
        desired_paused = _should_pause_capture(state)
        if desired_paused == state.capture_emit_paused:
            return
        sig_to_send = signal.SIGHUP if desired_paused else signal.SIGCONT
        if signal_capture(sig_to_send):
            state.capture_emit_paused = desired_paused

    def _recover_unmatched_from_cache(self, payload: dict) -> dict:
        """When a vinyl or AirPlay event arrives with no title, prefer the
        track on screen for that same source if we have one: the active
        vinyl prediction, else the last cached track. Prevents flickering to
        "Unknown" during volume changes or transport-only events that lack
        metadata. Returns the (possibly replaced) payload.
        """
        state = self.state
        if payload["source"] not in ("vinyl", "airplay") or payload["title"]:
            return payload
        cached = state.last_vinyl
        if payload["source"] == "vinyl":
            # last_vinyl is the last confirmed track; publishing it over a
            # prediction would move the display back and relabel the
            # predicted history row as confirmed.
            cached = self._current_prediction_payload(state, "vinyl") or cached
        if cached is not None and cached.get("source") == payload["source"]:
            return {**cached, "ts": payload["ts"], "state": payload["state"]}
        payload["match_method"] = "unmatched"
        return payload

    def _reset_for_non_idle_source(self, payload: dict) -> None:
        """Every event on a no-idle-handling source (TV, radio, unknown):
        keep the session state empty and cancel any idle task, whatever
        set them since the source change.
        """
        state = self.state
        if payload["source"] in ("vinyl", "airplay", "streaming"):
            return
        if state.user_track_pin is not None:
            log.info("pin released: reason=source_flip new_source=%s", payload["source"])
        state.reset_vinyl_session()
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
        # Capture edges describe the turntable. Every other source is
        # published from Sonos events, so an edge that slips through before
        # capture is paused must not arm the vinyl idle or publish.
        if state.sonos_source != "vinyl" or state.capture_emit_paused:
            return
        if s == "silent":
            self._handle_capture_silent()
            return
        if s == "audible":
            await self._handle_capture_audible()

    def _handle_capture_silent(self) -> None:
        """Sustained silence → arm idle timer that clears the kiosk.
        The active prediction is kept: five seconds of quiet is as often
        the gap between two tracks as a side flip, and the next audible
        edge must advance from the predicted track, not from the last
        confirmed one. The idle timer clears it when the quiet lasts.
        """
        state = self.state
        self._record_audible_edge_for_llm(state, "silent")
        state.quiet_began_at = (
            datetime.now(timezone.utc) - timedelta(seconds=SILENT_EDGE_DELAY_S)
        )
        if state.idle_task is not None and not state.idle_task.done():
            return
        state.idle_task = asyncio.create_task(
            self._idle_after_delay(VINYL_IDLE_DELAY_S, "vinyl"),
        )

    async def _handle_capture_audible(self) -> None:
        """Audible IPC event: sound is back. While a side timeline is
        followed it decides what the quiet was (a track boundary, a flip or
        a quiet passage). Otherwise this is "new record / side started":
        clear per-side recognition state, then either advance the predicted
        tracklist (album-locked) or publish a spinner transition (no lock).
        """
        state = self.state
        bcast = self.bcast
        self._record_audible_edge_for_llm(state, "audible")
        quiet_began_at = state.quiet_began_at
        state.quiet_began_at = None
        state.pending_shazam_only.clear()
        # A fingerprint anchor describes the audio before this edge. Past a
        # quiet gap it must not hold the display on that track or tag new
        # refs with it; the next strong match on fresh audio sets it again.
        state.fingerprint_anchor = None
        if await self._follow_timeline_on_sound(quiet_began_at):
            return
        state.track_started_at = None
        log.info("audible: cleared per-side recognition state")
        audio_source_label = "vinyl"
        if state.last_vinyl is not None:
            # Cancel any in-flight idle timer FIRST so it doesn't
            # fire and wipe the lock out from under the prediction
            # we're about to publish.
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

    async def _follow_timeline_on_sound(
        self, quiet_began_at: datetime | None,
    ) -> bool:
        """Let the side timeline decide what the quiet that just ended was,
        and carry out its action. Returns False when no timeline is being
        followed (the caller then behaves as it did before timelines).
        """
        state = self.state
        tl = state.side_timeline
        if tl is None:
            return False
        tracks = self._load_locked_tracks(state)
        if tracks is None:
            # The lock or its track list is gone: nothing to follow.
            state.side_timeline = None
            return False
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
            state.idle_task = None
        was = tl.position
        action = side_timeline.on_sound_returned(
            tl, tracks, quiet_began_at, datetime.now(timezone.utc),
        )
        start = iso_z(tl.started_at)
        if action is side_timeline.Action.RETIME:
            log.info("timeline: retime position=%s start=%s", tl.position, start)
            await self._retime_current_track(start)
        elif action is side_timeline.Action.IGNORE:
            log.info(
                "timeline: ignored gap position=%s (outside window)", tl.position,
            )
        else:
            if action is side_timeline.Action.FLIP:
                log.info(
                    "timeline: flip side=%s position=%s", tl.side, tl.position,
                )
            else:
                log.info(
                    "timeline: advance side=%s position=%s start=%s (gap)",
                    tl.side, tl.position, start,
                )
            if not await self._try_advance_prediction(
                state, "vinyl", self.bcast,
                target_track_position=tl.position,
                track_started_at_override=start,
            ):
                log.warning(
                    "timeline: could not show position=%s (was %s)",
                    tl.position, was,
                )
        return True

    async def _retime_current_track(self, start: str) -> None:
        """Move the start of the track on screen to ``start`` and publish it
        again: the gap that belongs to a run-out boundary has arrived. No
        play is recorded: the run-out already recorded this track, and after
        a long quiet a second record would be a second history row.
        """
        state = self.state
        state.track_started_at = start
        if state.predicted_position is not None:
            state.predicted_position["track_started_at"] = start
            payload = self._current_prediction_payload(state, "vinyl")
            if payload is not None:
                await self.bcast.publish(self._anchor_and_publish(payload))
            return
        if state.last_vinyl is not None:
            state.last_vinyl["track_started_at"] = start
            await self.bcast.publish(self._anchor_and_publish(state.last_vinyl))

