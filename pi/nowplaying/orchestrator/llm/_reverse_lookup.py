"""LLM-assisted reverse-lookup disambiguation.

Extracted from ``_llm_hooks.py``.  Owns: ``_winner_payload_from_result``,
``_swap_result_fields``, and the ``_maybe_llm_disambiguate_reverse_lookup``
family of helpers on ``LLMHooksMixin``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nowplaying.llm.release import ReleasePick
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


def _winner_payload_from_result(result: dict) -> dict:
    """Shape a Shazam ``result`` row to match the alternate-release
    payload structure expected by ``_build_reverse_lookup_prompt``.
    """
    return {
        "release_id": result.get("release_id"),
        "artist": result.get("artist"),
        "album": result.get("album"),
        "year": result.get("year"),
        "format": result.get("format"),
        "matched_track_position": result.get("track_position"),
        "matched_track_title": result.get("title"),
        "score": result.get("match_score"),
    }


def _swap_result_fields(
    result: dict, new_rel: dict, chosen_alt: dict,
) -> None:
    """Mutate ``result`` in place to reflect a swap to a new release.

    Mirrors ``recognize_proto._release_fields`` shape so downstream
    consumers see the same payload structure they'd see from a direct
    catalog lookup. ``chosen_alt`` carries the matched_track_position
    on the new release (already collected by the catalog).
    """
    result["release_id"] = new_rel.get("id")
    result["album"] = new_rel.get("title")
    result["year"] = new_rel.get("year")
    result["label"] = new_rel.get("label")
    result["catno"] = new_rel.get("catno")
    result["art_path"] = new_rel.get("art_path")
    result["tracklist"] = [
        {
            "position": t["position"],
            "side": t["side"],
            "title": t["title"],
            "duration_seconds": t["duration_seconds"],
        }
        for t in (new_rel.get("tracks") or [])
    ]
    new_pos = chosen_alt.get("track_position")
    if new_pos:
        result["track_position"] = new_pos
    new_title = chosen_alt.get("track_title")
    if new_title:
        result["title"] = new_title


def _seconds_since_confirm(state: "State") -> float | None:
    """Monotonic seconds since the last confidence stamp; None when
    unstamped (cold start) or no running loop (test contexts)."""
    if state.last_vinyl_confidence_set_at is None:
        return None
    try:
        return (
            asyncio.get_running_loop().time()
            - state.last_vinyl_confidence_set_at
        )
    except RuntimeError:  # no running loop (test context)
        return None


class ReverseLookupMixin:
    """LLM-assisted reverse-lookup disambiguation methods."""

    async def _maybe_llm_disambiguate_reverse_lookup(
        self, result: dict, state: "State",
    ) -> None:
        """Consult ``judge_reverse_lookup`` when the catalog attached
        alternates (winner and 1+ alternates within ~20 points). The
        hook picks ONE release_id; on a valid non-winner pick we swap
        the relevant ``result`` fields in place.

        Runs BEFORE ``_llm_rejects_shazam_match`` so relevance evaluates
        the (possibly swapped) winner. See
        docs/features/llm-assisted-reverse-lookup/.
        """
        if not self.llm.enabled:
            return
        alternates = result.get("alternate_releases") or []
        winner_rid = result.get("release_id")
        if not alternates or winner_rid is None:
            return
        verdict = await self._call_reverse_lookup_judge(result, state, alternates)
        if verdict is None:
            return
        valid_rids = {winner_rid} | {
            a.get("release_id")
            for a in alternates
            if a.get("release_id") is not None
        }
        self._apply_reverse_lookup_verdict(
            result, alternates, winner_rid, valid_rids, verdict,
        )

    async def _call_reverse_lookup_judge(  # skylos: ignore SKY-L006 — Why: returns None on exception/USE_HEURISTIC and returns the verdict object otherwise; both branches are explicit and the return type annotation ReleasePick | None documents the contract
        self, result: dict, state: "State", alternates: list[dict],
    ) -> "ReleasePick | None":
        """Build context + invoke the LLM. Returns the verdict or None
        on USE_HEURISTIC / exception. Pulled out to keep the parent
        method under the complexity threshold."""
        from nowplaying.llm import USE_HEURISTIC
        ctx = await self._build_reverse_lookup_ctx(result, state)
        winner_payload = _winner_payload_from_result(result)
        try:
            verdict = await self.llm.judge_reverse_lookup(
                winner_payload, alternates, ctx,
            )
        except Exception as e:  # noqa: BLE001  # Why — broad catch to degrade gracefully on any LLM error
            log.warning(
                "reverse-lookup-llm: judge raised %r; keeping heuristic", e,
            )
            return None
        if verdict is USE_HEURISTIC:
            return None
        return verdict

    async def _build_reverse_lookup_ctx(
        self, result: dict, state: "State",
    ) -> dict:
        """Assemble the LLM context dict (locked album + recent history
        + time-since-confirm + Shazam query fields)."""
        from nowplaying import history as history_mod
        locked = state.last_vinyl or {}
        seconds_since_confirm = _seconds_since_confirm(state)
        try:
            recent_history = await asyncio.to_thread(history_mod.queries.recent, 10)
        except Exception as e:  # noqa: BLE001  # Why — degrade gracefully; history is optional context
            log.warning("reverse-lookup-llm: history.recent failed: %r", e)
            recent_history = []
        return {
            "query_artist": result.get("artist"),
            "query_title": result.get("title"),
            "query_isrc": result.get("isrc"),
            "locked_release_id": locked.get("release_id"),
            "locked_album_title": locked.get("album"),
            "locked_artist": locked.get("artist"),
            "locked_track_position": locked.get("track_position"),
            "locked_track_title": locked.get("title"),
            "seconds_since_last_confirm": seconds_since_confirm,
            "recent_history": recent_history,
        }

    def _apply_reverse_lookup_verdict(
        self,
        result: dict,
        alternates: list[dict],
        winner_rid: int,
        valid_rids: set,
        verdict,
    ) -> None:
        """Validate the verdict's release_id and swap ``result`` if it
        points to a different (and known-good) alternate."""
        from nowplaying.discogs import catalog as discogs_catalog
        picked_rid = verdict.release_id
        if picked_rid not in valid_rids:
            log.warning(
                "reverse-lookup-llm: hallucinated release_id=%s not in "
                "candidates %s; keeping heuristic winner %s",
                picked_rid, sorted(v for v in valid_rids if v is not None), winner_rid,
            )
            return
        if picked_rid == winner_rid:
            log.info(
                "reverse-lookup-llm: confirmed heuristic winner %s — %s",
                winner_rid, verdict.reason,
            )
            return
        chosen_alt = next(
            (a for a in alternates if a.get("release_id") == picked_rid),
            None,
        )
        new_rel = discogs_catalog.get_release(picked_rid) if chosen_alt else None
        if new_rel is None:
            log.warning(
                "reverse-lookup-llm: picked release_id=%s not found in "
                "catalog; keeping heuristic winner %s",
                picked_rid, winner_rid,
            )
            return
        log.info(
            "reverse-lookup-llm: swapped from rid=%s to rid=%s — %s",
            winner_rid, picked_rid, verdict.reason,
        )
        _swap_result_fields(result, new_rel, chosen_alt)
