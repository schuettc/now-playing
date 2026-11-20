"""Predicted-advance helpers — resolve and publish the next track.

Contains: follow_match_on_timeline, _republish_current_prediction,
_resolve_advanced_track, _try_advance_prediction, _load_locked_tracks,
_resolve_advance_source_position.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from nowplaying import catalog as catalog_dispatch, history
from nowplaying.orchestrator import side_timeline
from nowplaying.orchestrator.prediction import (
    _advance_predicted_position,
    _build_predicted_payload,
    enrich_guess_contract,
)

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


# Successive matches on one track each give a start from their own offset;
# within this many seconds of the timeline's start they are the same start,
# and the timeline's is kept so the published start does not jitter.
SAME_START_TOLERANCE_S = 2.0


def iso_z(when: datetime) -> str:
    """UTC ISO-8601 to the second with a Z, the form track starts are
    published in."""
    return (
        when.astimezone(timezone.utc)
        .isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def follow_match_on_timeline(
    state: "State", *, started_at: str | None, supplied_start: bool,
) -> str | None:
    """Apply a match (Shazam, fingerprint, pin or a user's pick) that has
    just been written to ``state.last_vinyl`` to the side timeline.

    A match on the timeline's current track leaves it alone, except that a
    start the match itself supplied (an offset into the song) replaces the
    timeline's when it differs by more than ``SAME_START_TOLERANCE_S``. A
    match on any other track rebuilds the timeline there, starting at
    ``started_at`` (now when missing). A match with no track position, or
    on a release with no track list, ends the timeline.

    Returns the timeline's start when the match named the current track and
    the timeline kept its own start, for the caller to show; otherwise None.
    """
    lv = state.last_vinyl or {}
    pos = lv.get("track_position")
    vinyl = lv.get("source") == "vinyl"
    rid = int(lv["release_id"]) if lv.get("release_id") is not None else None
    mbid = lv.get("release_mbid")
    start = _parse_iso(started_at)
    tl = state.side_timeline
    if (
        tl is not None and vinyl and pos and tl.position == pos
        and (tl.release_id, tl.release_mbid) == (rid, mbid)
    ):
        if (
            supplied_start and start is not None
            and abs((start - tl.started_at).total_seconds()) > SAME_START_TOLERANCE_S
        ):
            tl.started_at = start
            tl.by_run_out = False
            return None
        return iso_z(tl.started_at)
    tracks = _AdvanceMixin._load_locked_tracks(state) if pos else None
    if tracks is None:
        state.side_timeline = None
        return None
    state.side_timeline = side_timeline.start_at(
        tracks, pos, start or datetime.now(timezone.utc), rid, mbid,
    )
    if state.side_timeline is not None:
        log.info(
            "timeline: set side=%s position=%s start=%s (match)",
            state.side_timeline.side, pos, iso_z(state.side_timeline.started_at),
        )
    return None


class _AdvanceMixin:
    """Predicted-advance helpers.  Mixed into PredictionMixin."""

    @staticmethod
    def _current_prediction_payload(state: "State", source: str) -> dict | None:
        """The payload for the prediction on screen, or None when there is no
        active prediction or it can't be assembled (catalog miss)."""
        if state.predicted_position is None or state.last_vinyl is None:
            return None
        payload = _build_predicted_payload(
            state.last_vinyl, state.predicted_position, source,
        )
        if payload is None:
            return None
        payload["guess"] = {
            "position": state.predicted_position["track_position"],
            "title": payload.get("title") or state.predicted_position.get("title"),
            "confidence": "medium",
            "source": "heuristic",
        }
        enrich_guess_contract(payload)
        return payload

    async def _republish_current_prediction(
        self, state: "State", source: str, broadcaster,
    ) -> bool:
        """Re-publish the currently-active prediction without advancing.

        Used when an unmatched streak persists past NEEDS_ID_STREAK
        and we want to keep the kiosk warm AND extend the history
        row's duration. No state change. Returns True if a payload was
        published, False if the prediction couldn't be assembled
        (catalog miss).
        """
        payload = self._current_prediction_payload(state, source)
        if payload is None:
            return False
        await broadcaster.publish(self._anchor_and_publish(payload))
        try:
            await history.record_play(payload)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "history.record_play (predicted re-publish) failed: %r", e
            )
        return True

    def _resolve_advanced_track(
        self, state: "State", tracks: list[dict],
        target_track_position: str | None,
    ) -> dict | None:
        """Pick the advanced track. F6 explicit-target path takes precedence;
        falls back to the source-position + advance-by-one heuristic when
        the target is missing or absent.
        """
        if target_track_position is not None:
            # Tracklist items use `position` in production (built by
            # recognize_proto, _tracklist_from_release, _publish_enrichment);
            # legacy paths use `track_position`. Accept both so the F6 LLM
            # advance hook can target by position regardless of how the
            # tracklist was assembled. Same defensive pattern as
            # _llm_hooks._make_title_for (commit a3d306d).
            advanced_row = next(
                (t for t in tracks
                 if (t.get("position") or t.get("track_position")) == target_track_position),
                None,
            )
            if advanced_row is not None:
                # Normalize to predicted-position shape (mirrors
                # _advance_predicted_position's return) so downstream
                # _build_predicted_payload can look the release up.
                # Without this normalization the raw tracklist-row shape
                # (no release_id) crashed callers.
                last_vinyl = state.last_vinyl or {}
                return {
                    "release_id": last_vinyl.get("release_id"),
                    "release_mbid": last_vinyl.get("release_mbid"),
                    "side": advanced_row.get("side") or last_vinyl.get("side"),
                    "track_position": (
                        advanced_row.get("position")
                        or advanced_row.get("track_position")
                    ),
                    "title": advanced_row.get("title"),
                    "duration_seconds": advanced_row.get("duration_seconds"),
                }
            log.warning(
                "advance-track: target_track_position=%r not in tracklist; "
                "falling back to heuristic",
                target_track_position,
            )
        source_pos = self._resolve_advance_source_position(state, tracks)
        if source_pos is None:
            return None
        return _advance_predicted_position(tracks, source_pos)

    async def _try_advance_prediction(
        self, state: "State", source: str, broadcaster,
        *, track_started_at_override: str | None = None,
        target_track_position: str | None = None,
    ) -> bool:
        """Advance the predicted tracklist position and publish.

        Called from the audible-edge handler when we have an active
        album lock. Returns True if a prediction was published, False
        if we couldn't advance (end-of-side, missing catalog data, or
        no last_vinyl track_position to seed from).

        Source-position selection:
          - If state.predicted_position is set, advance from it.
          - Else seed from state.last_vinyl["track_position"] +
            ["side"] — find that position's index_in_side via the
            catalog tracklist, then advance.

        ``track_started_at_override`` overrides the per-method
        RECOGNITION_LEAD_S back-date. Used by the streak-seeded
        publish path, where the song has actually been playing for
        ~30s by the time NEEDS_ID_STREAK trips — using the 2s
        ``predicted`` lead would leave lyrics ~28s ahead of audio.

        ``target_track_position`` (F6 LLM advance hook): when provided,
        skip the advance-by-one heuristic and pick the track whose
        track_position matches exactly. Falls back to today's heuristic
        if the target isn't found in the tracklist.
        """
        tracks = self._load_locked_tracks(state)
        if tracks is None:
            return False
        advanced = self._resolve_advanced_track(state, tracks, target_track_position)
        if advanced is None:
            return False
        payload = _build_predicted_payload(state.last_vinyl, advanced, source)
        if payload is None:
            return False
        if track_started_at_override is not None:
            payload["track_started_at"] = track_started_at_override
        # Commit state + publish.
        state.predicted_position = advanced
        state.unmatched_streak = 0
        advanced_pos = advanced.get("track_position")
        if advanced_pos:
            state.tracks_seen_since_audible_edge.add(advanced_pos)
        payload["guess"] = {
            "position": advanced["track_position"],
            "title": payload.get("title") or advanced.get("title"),
            "confidence": "medium",
            "source": "heuristic",
        }
        enrich_guess_contract(payload)
        log.info(
            "predicted: advanced to side=%s position=%s title=%r",
            advanced["side"], advanced["track_position"],
            payload.get("title"),
        )
        published = self._anchor_and_publish(payload)
        # Stamp the predicted track's own start and length onto the prediction:
        # the track guess, state-decay and a later pin's elapsed-aware TTL all
        # measure the track on screen from these. The start is the one the
        # publish settled on (the override, or the audible-edge back-date).
        # See docs/features/advance-on-shazam-quiet-records/.
        advanced["track_started_at"] = published.get("track_started_at")
        advanced["duration_seconds"] = published.get("duration_seconds")
        await broadcaster.publish(published)
        # Why: refresh the confidence stamp so state-decay (which measures
        # age since last_vinyl_confidence_set_at) doesn't kill this fresh
        # predicted publish ~15s later — the prior stamp was set by the
        # last Shazam confirm on the *previous* track. Re-publishes via
        # _republish_current_prediction intentionally do NOT refresh so
        # state-decay still fires eventually if no real recognition arrives.
        state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time()
        try:
            await history.record_play(payload)
        except Exception as e:  # noqa: BLE001
            log.warning("history.record_play (predicted) failed: %r", e)
        return True

    @staticmethod
    def _load_locked_tracks(state: "State") -> list[dict] | None:
        """Return the catalog tracklist for the currently-locked release,
        or None when no lock exists / catalog miss / empty tracklist.
        last_vinyl also caches the current AirPlay or streaming track for
        the repoll; only a vinyl payload is an album lock.
        """
        if state.last_vinyl is None:
            return None
        if state.last_vinyl.get("source") != "vinyl":
            return None
        locked_rid = state.last_vinyl.get("release_id")
        locked_mbid = state.last_vinyl.get("release_mbid")
        if locked_rid is None and not locked_mbid:
            return None
        release = catalog_dispatch.get_release(
            release_id=int(locked_rid) if locked_rid is not None else None,
            mbid=locked_mbid,
        )
        if release is None:
            return None
        tracks = release.get("tracks") or []
        if not tracks:
            return None
        return tracks

    @staticmethod
    def _resolve_advance_source_position(
        state: "State", tracks: list[dict],
    ) -> dict | None:
        """Determine the position to advance from. If predicted_position
        is set, use it. Else seed from last_vinyl's confirmed position
        by locating it in the catalog tracklist. Returns None when the
        confirmed position can't be located.
        """
        if state.predicted_position is not None:
            return state.predicted_position
        confirmed_pos = state.last_vinyl.get("track_position")
        confirmed_side = state.last_vinyl.get("side")
        if not confirmed_pos or not confirmed_side:
            return None
        side_tracks = [t for t in tracks if t.get("side") == confirmed_side]
        idx = next(
            (
                i for i, t in enumerate(side_tracks)
                if t.get("position") == confirmed_pos
            ),
            None,
        )
        if idx is None:
            return None
        # release_id stays an int when present (Discogs path); discovered
        # releases pass through as None and predicted_position carries
        # release_mbid alongside.
        rid_raw = state.last_vinyl.get("release_id")
        rid_int = int(rid_raw) if rid_raw is not None else None
        return {
            "release_id": rid_int,
            "release_mbid": state.last_vinyl.get("release_mbid"),
            "side": confirmed_side,
            "track_position": confirmed_pos,
            "index_in_side": idx,
        }


