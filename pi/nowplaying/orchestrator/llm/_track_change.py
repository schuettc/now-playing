"""LLM track-change primary judge + context helpers.

Extracted from ``_llm_hooks.py``.  Owns: ``_maybe_llm_override_rule_a``,
``_record_fp_hit_for_llm``, ``_record_shazam_for_llm``,
``_record_audible_edge_for_llm``, ``_build_track_change_llm_context``.
See docs/features/llm-track-change-primary/ for the design.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from nowplaying.orchestrator.advance import (
    _compute_advance_elapsed_s,
    _interpret_advance_verdict,
)
from nowplaying.orchestrator.streaming_idle import (
    HEARTBEAT_INTERVAL_S,
    NEEDS_ID_STREAK,
    RECOGNITION_LEAD_S,
)

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


class TrackChangeMixin:
    """LLM track-change judge and context recorder methods."""

    async def _maybe_consult_llm_for_advance(
        self, state: "State", seed_back_s: float,
    ) -> str | None:
        """Consult the LLM for which track to advance to. Returns:
          - None: LLM disabled, no usable side context, or sentinel /
            error path — use today's heuristic.
          - "STAY": LLM said the needle is still on last_vinyl;
            caller should bail without seeding a prediction.
          - track_position str: explicit target track_position to use.
        """
        if not self.llm.enabled or state.last_vinyl is None:
            return None
        locked_side = state.last_vinyl.get("side")
        if not locked_side:
            return None
        all_tracks = self._load_locked_tracks(state)
        if all_tracks is None:
            return None
        side_tracklist = [
            t for t in all_tracks if t.get("side") == locked_side
        ]
        if len(side_tracklist) < 2:
            return None  # nothing to choose between
        elapsed_s = _compute_advance_elapsed_s(
            state.track_started_at, seed_back_s,
        )
        verdict = await self.llm.judge_advance(
            elapsed_s=elapsed_s,
            last_track={
                "title": state.last_vinyl.get("title"),
                "track_position": state.last_vinyl.get("track_position"),
                "side": state.last_vinyl.get("side"),
            },
            side_tracklist=side_tracklist,
        )
        return _interpret_advance_verdict(
            verdict, side_tracklist, state.last_vinyl.get("title"),
        )

    async def _maybe_llm_override_rule_a(  # skylos: ignore SKY-Q301 SKY-C304 — Why: CC 12 / 85 lines are sequential verdict-validation gates (USE_HEURISTIC, confidence, position existence, tracklist membership); each gate is a distinct safety check and extracting them would scatter a single decision flow across many tiny helpers
        self,
        state: "State",
        audio_source_label: str,
        elapsed_s: float,
    ) -> bool:
        """Consult Haiku on an ambiguous Rule A suppression. Returns True
        when the LLM confidently advances and the orchestrator should return
        early (i.e. the caller should skip the NEEDS_ID fall-through).

        Called ONLY from the Rule A mid-track suppression branch — that is,
        only when deterministic rules would suppress an advance. Unambiguous
        Rule A decisions (near-end-of-track or non-pinned streak) never reach
        this method.

        On USE_HEURISTIC, timeout, error, or low-confidence verdict → returns
        False so the caller falls through to NEEDS_ID (unchanged Rule A behavior).
        """
        from nowplaying.llm import USE_HEURISTIC
        context = self._build_track_change_llm_context(state, elapsed_s)
        verdict = await self.llm.decide_track_change(context)
        if verdict is USE_HEURISTIC:
            log.info("track-change-llm: no verdict (USE_HEURISTIC) — Rule A holds")
            return False

        log.info(
            "track-change-llm: decision=%s confidence=%.2f advance_to=%r reason=%r",
            verdict.decision, verdict.confidence,
            verdict.advance_to_position, verdict.reason,
        )

        if verdict.decision != "advance" or verdict.confidence < 0.7:
            log.info(
                "track-change-llm: holding (decision=%s confidence=%.2f < 0.7 threshold)",
                verdict.decision, verdict.confidence,
            )
            return False

        # Sanity check: advance_to_position must exist on the locked tracklist.
        target = verdict.advance_to_position
        if not target:
            log.warning(
                "track-change-llm: advance decision missing advance_to_position — downgrading to hold",
            )
            return False

        all_tracks = self._load_locked_tracks(state)
        locked_side = (state.last_vinyl or {}).get("side")
        if all_tracks and locked_side:
            side_positions = {
                t.get("track_position") or t.get("position")
                for t in all_tracks
                if t.get("side") == locked_side
            }
        else:
            side_positions = set()

        if target not in side_positions:
            log.warning(
                "track-change-llm: advance_to_position=%r not in tracklist "
                "positions=%r — downgrading to hold",
                target, sorted(side_positions),
            )
            return False

        log.info(
            "track-change-llm: advancing to position=%r (confidence=%.2f)",
            target, verdict.confidence,
        )
        # Back-date track_started_at by NEEDS_ID_STREAK heartbeats (same as
        # _seed_prediction_from_last_vinyl) so the client-side elapsed clock
        # is accurate.
        seed_back_s = (
            NEEDS_ID_STREAK * HEARTBEAT_INTERVAL_S
            + RECOGNITION_LEAD_S.get("predicted", 2)
        )
        seed_anchor = (
            datetime.now(timezone.utc) - timedelta(seconds=seed_back_s)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        advanced = await self._try_advance_prediction(
            state, audio_source_label, self.bcast,
            track_started_at_override=seed_anchor,
            target_track_position=target,
        )
        return advanced

    @staticmethod
    def _record_fp_hit_for_llm(state: "State", hits: list) -> None:
        """Append the top fingerprint hit (if any) to `state.recent_fp_hits`.

        Records even sub-threshold hits because the LLM uses this history
        to understand which positions the fingerprint engine is seeing, not
        just confirmed matches.  Keeps the last 10 entries.
        """
        if not hits:
            return
        top = hits[0]
        now = asyncio.get_event_loop().time()
        state.recent_fp_hits.append({
            "position": top.track_position,
            "hits": top.hits,
            "ts": now,
        })
        # Keep last 10 entries — a rolling window of ~10 heartbeats (≈150s).
        if len(state.recent_fp_hits) > 10:
            state.recent_fp_hits = state.recent_fp_hits[-10:]

    @staticmethod
    def _record_shazam_for_llm(state: "State", result: dict) -> None:
        """Store the most recent Shazam result for LLM track-change context.

        Recorded before gate checks so even gated/low-confidence Shazam
        evidence is available to the LLM (that's the point — the LLM can
        weight ambiguous signals that the deterministic rules ignore).
        """
        state.last_shazam_gated = {
            "artist": result.get("artist"),
            "title": result.get("title"),
            "release_id": result.get("release_id"),
        }

    @staticmethod
    def _record_audible_edge_for_llm(state: "State", edge_type: str) -> None:
        """Append a silent/audible edge event to `state.recent_audible_edges`.

        Prunes entries older than 60s so the list stays bounded.
        `edge_type` must be "audible" or "silent".
        """
        now_iso = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        now_mono = asyncio.get_event_loop().time()
        # Prune to 60s window first.
        state.recent_audible_edges = [
            e for e in state.recent_audible_edges
            if now_mono - e.get("_ts_mono", now_mono) < 60.0
        ]
        state.recent_audible_edges.append({
            "type": edge_type,
            "ts_iso": now_iso,
            "_ts_mono": now_mono,
        })
        if edge_type == "audible":
            state.tracks_seen_since_audible_edge = set()
            # Power _compute_elapsed_since_audible_up_s so the LLM track-guess
            # prompt has an "elapsed since needle drop" clock that survives
            # mid-side predicted-advance refreshes of track_started_at.
            # See docs/features/llm-track-guess-elapsed-frame-confusion/.
            state.audible_up_at_mono = now_mono

    def _build_track_change_llm_context(
        self, state: "State", elapsed_s: float,
    ) -> dict:
        """Assemble the LLM context dict for `decide_track_change`.

        Called from the Rule A suppression branch when `ANTHROPIC_API_KEY`
        is set.  All fields are best-effort — missing catalog data degrades
        gracefully to empty lists / None.
        """
        locked = state.last_vinyl or {}
        locked_track = {
            "release_id": locked.get("release_id"),
            "title": locked.get("title"),
            "position": locked.get("track_position"),
            "duration_s": locked.get("duration_seconds"),
        }
        # Build full tracklist for the sanity check and LLM context.
        all_tracks = self._load_locked_tracks(state)
        locked_side = locked.get("side")
        if all_tracks and locked_side:
            side_tracks = [
                t for t in all_tracks if t.get("side") == locked_side
            ]
            full_tracklist = [
                {
                    "position": t.get("track_position") or t.get("position"),
                    "title": t.get("title"),
                    "duration_s": t.get("duration_seconds"),
                }
                for t in side_tracks
            ]
        else:
            full_tracklist = []
        # Strip the internal monotonic timestamp from edge entries —
        # the LLM only needs the ISO timestamp and event type.
        edges = [
            {"type": e["type"], "ts_iso": e["ts_iso"]}
            for e in state.recent_audible_edges
        ]
        # FP hits: strip internal monotonic ts, keep position + hits.
        fp_hits = [
            {"position": h["position"], "hits": h["hits"]}
            for h in state.recent_fp_hits
        ]
        return {
            "locked_track": locked_track,
            "elapsed_since_last_signal_s": elapsed_s,
            "recent_fp_hits": fp_hits,
            "last_shazam_gated": state.last_shazam_gated,
            "recent_audible_edges": edges,
            "full_tracklist": full_tracklist,
        }
