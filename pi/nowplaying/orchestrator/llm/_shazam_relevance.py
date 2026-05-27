"""LLM-assisted Shazam relevance hook (F5).

Extracted from ``_llm_hooks.py``.  Owns: ``_should_consult_llm_for_shazam``,
``_build_locked_album_ctx``, and ``_llm_rejects_shazam_match``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nowplaying.orchestrator.state import State

log = logging.getLogger("nowplaying.main")


class ShazamRelevanceMixin:
    """LLM-assisted Shazam relevance filter methods."""

    def _should_consult_llm_for_shazam(
        self, result: dict, state: "State",
    ) -> bool:
        """Gate the F5 LLM-shazam-relevance hook.

        Returns True only when a real cross-album disagreement exists
        between the Shazam result and the currently-locked album. Skip:
          - LLM disabled (no key set)
          - Nothing locked yet
          - Same release_id (definitely same album)
          - Same artist case-insensitive (likely different track on same
            artist; today's path handles this fine and asking the LLM
            here would be needless cost)

        Returns True when neither release_id nor artist agrees with the
        lock — i.e., the kiosk is about to flip to a different album/
        artist on this heartbeat, which is exactly when noise-vs-signal
        judgment matters.
        """
        from nowplaying.orchestrator.shazam_match import _shazam_disagrees_with_lock
        if not self.llm.enabled or state.last_vinyl is None:
            return False
        return _shazam_disagrees_with_lock(result, state.last_vinyl)

    @staticmethod
    def _build_locked_album_ctx(state: "State") -> dict | None:
        """Project the locked album's identifying fields into the shape
        `LLMAssist.judge_shazam_result` expects. Returns None when no lock
        is set (matches the prompt-builder's "locked=False" branch)."""
        locked = state.last_vinyl
        if locked is None:
            return None
        return {
            "locked_artist": locked.get("artist"),
            "locked_album": locked.get("album"),
            "locked_release_id": locked.get("release_id"),
            "locked_title": locked.get("title"),
        }

    async def _llm_rejects_shazam_match(
        self, result: dict, state: "State",
    ) -> bool:
        """F5 LLM-shazam-relevance hook. Returns True only when the LLM
        explicitly rejects the Shazam result on a cross-album disagreement.
        Fires before the confirmed-match side effects so a rejected
        verdict can't leak state changes.
        See docs/features/llm-shazam-relevance/.
        """
        if not self._should_consult_llm_for_shazam(result, state):
            return False
        from nowplaying.llm import USE_HEURISTIC
        verdict = await self.llm.judge_shazam_result(
            shazam_track=result,
            locked_album_ctx=self._build_locked_album_ctx(state),
        )
        if verdict is USE_HEURISTIC or verdict.accept:
            return False
        log.info("shazam-relevance: rejected — %s", verdict.reason)
        return True
