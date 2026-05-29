"""`LLMAssist` class plus shared module-level plumbing.

Holds the optional Anthropic-Haiku assistant: cache, throttled error
warnings, lazy SDK client, the `_invoke` tool-use call, and the four
public judge methods. Per-judge dataclasses, tool specs, and prompt
builders live in their own sibling modules and are re-exported via the
package `__init__.py`.

`USE_HEURISTIC` is the SINGLE module-level sentinel for the entire
package; consumers MUST import it from `nowplaying.llm` (or this
module) so identity checks (`x is USE_HEURISTIC`) work.

The `anthropic` SDK is lazy-imported inside `_get_client` so the
disabled path (and any consumer test that mocks the client) never
pays the import cost.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

from nowplaying.llm.advance import (
    AdvanceVerdict,
    _ADVANCE_TOOL_SPEC,
    _build_advance_prompt,
)
from nowplaying.llm.release import (
    _RANK_RELEASES_TOOL_SPEC,
    _REVERSE_LOOKUP_TOOL_SPEC,
    _build_rank_releases_prompt,
    _build_reverse_lookup_prompt,
    _parse_release_pick,
    _parse_release_ranking,
)
from nowplaying.llm.shazam import (
    ShazamVerdict,
    _SHAZAM_TOOL_SPEC,
    _build_shazam_prompt,
)
from nowplaying.llm.track_change import (
    TrackChangeVerdict,
    _TRACK_CHANGE_TOOL_SPEC,
    _build_track_change_prompt,
    _parse_track_change,
)
from nowplaying.llm.title_clean import (
    CleanTitle,
    _CLEAN_TITLE_TOOL_SPEC,
    _build_clean_title_prompt,
)
from nowplaying.llm.track_guess import (
    _TRACK_GUESS_TOOL_SPEC,
    _build_track_guess_prompt,
    _parse_track_guess,
)

log = logging.getLogger("nowplaying.llm")

# Sentinel returned when the LLM is disabled, errors out, or — in this
# F4 scaffold — when `_invoke` reaches its terminal return. Call sites
# check identity (`if result is USE_HEURISTIC:`) to decide whether to
# fall back to the hardcoded path.
USE_HEURISTIC: Any = object()

# Anthropic model + client tuning. Module-top consts so the swap surface
# is one line each if Anthropic ships a newer Haiku or we tune budgets.
MODEL_ID = "claude-haiku-4-5"
TIMEOUT_S = 3.0
CACHE_TTL_S = 300.0  # 5 minutes
ERROR_WARN_INTERVAL_S = 300.0  # rate-limit repeat warnings


class LLMAssist:
    """Optional LLM-assisted decision points. No-op when disabled.

    Read `ANTHROPIC_API_KEY` once at construction; absent → `enabled = False`
    and every public method returns `USE_HEURISTIC` without touching the
    Anthropic SDK. Setting the key after construction has no effect (the
    orchestrator is process-long-lived).
    """

    def __init__(self) -> None:
        key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        self.enabled: bool = bool(key)
        self._api_key: str = key
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_warned: dict[type, float] = {}
        # AsyncAnthropic instance is lazily created on first _invoke call when
        # enabled — defers the optional `anthropic` import to call time so the
        # disabled path is free. Reused across calls to amortize the
        # connection-pool setup (F4 impl-review should-fix).
        self._client: Any = None

    # ── Public hooks ────────────────────────────────────────────────────
    #
    # Each method assembles a method-name-prefixed prompt, calls _invoke,
    # and either returns the parsed verdict (F5–F8) or — in this scaffold
    # — passes USE_HEURISTIC through. F5–F8 swap `_invoke`'s terminal
    # return for parsed structured output without changing call sites.

    async def judge_shazam_result(
        self,
        shazam_track: dict[str, Any],
        locked_album_ctx: dict[str, Any] | None,
    ) -> Any:
        """Decide whether a Shazam hit is a real new-record signal or
        cover/sample/wrong-album noise.

        Called from `Orchestrator` on Shazam confirms that disagree with the
        currently-locked album (different release_id AND different artist).
        See `docs/features/llm-shazam-relevance/plan.md` for the gate logic.

        Returns:
            ShazamVerdict on success (accept publishes Shazam result; reject
              swallows the heartbeat).
            USE_HEURISTIC when LLM disabled or any error path triggers.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_shazam_prompt(shazam_track, locked_album_ctx)
        return await self._invoke(
            "judge_shazam_result",
            prompt,
            ShazamVerdict,
            tool_spec=_SHAZAM_TOOL_SPEC,
        )

    async def judge_advance(
        self,
        elapsed_s: float,
        last_track: dict[str, Any] | None,
        side_tracklist: list[dict[str, Any]],
    ) -> Any:
        """Decide which track on the locked side the needle is on now.

        Called from `Orchestrator._seed_prediction_from_last_vinyl` when
        the unmatched-heartbeat streak hits NEEDS_ID_STREAK and the kiosk
        needs a predicted position. The LLM returns either an index into
        `side_tracklist` (the orchestrator translates that to a target
        track_position) or `None` to stay on `last_track`.

        Returns:
            AdvanceVerdict(advance_to_index, reason) on success.
            USE_HEURISTIC when LLM disabled or any error.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_advance_prompt(elapsed_s, last_track, side_tracklist)
        return await self._invoke(
            "judge_advance", prompt, AdvanceVerdict,
            tool_spec=_ADVANCE_TOOL_SPEC,
        )

    async def rank_releases(
        self,
        candidates: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> Any:
        """Re-rank Discogs release candidates by query + locked-album context.

        Called from `/api/collection/search` after the heuristic ranking
        when 2+ candidates pass threshold. Returns a `ReleaseRanking`
        whose `release_ids` is the LLM's preferred order. The caller
        reorders the items list using these ids and leaves any
        candidate not mentioned by the LLM in its heuristic position.

        Returns:
            ReleaseRanking(release_ids=tuple[int, ...], reason=str) on success.
            USE_HEURISTIC when LLM disabled or any error.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_rank_releases_prompt(candidates, ctx)
        verdict = await self._invoke(
            "rank_releases", prompt, _parse_release_ranking,
            tool_spec=_RANK_RELEASES_TOOL_SPEC,
        )
        return verdict

    async def judge_reverse_lookup(
        self,
        winner: dict[str, Any],
        alternates: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> Any:
        """Pick ONE release_id from a heuristic-tied candidate set —
        the catalog's reverse-lookup found a winner plus alternates
        within ~20 points, and the orchestrator needs disambiguation
        (especially for record-flip scenarios where the +25 sticky
        bonus would actively misroute).

        Called from the orchestrator's Shazam-confirmation path BEFORE
        the relevance check (so relevance evaluates the chosen pressing,
        not the heuristic's pre-disambiguation pick). Caller validates
        the returned release_id against the {winner} ∪ {alt_ids} set
        before applying it — hallucinated IDs are dropped.

        See docs/features/llm-assisted-reverse-lookup/.

        Returns:
            ReleasePick(release_id, reason) on success.
            USE_HEURISTIC when LLM disabled or any error path triggers.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_reverse_lookup_prompt(winner, alternates, ctx)
        verdict = await self._invoke(
            "judge_reverse_lookup", prompt, _parse_release_pick,
            tool_spec=_REVERSE_LOOKUP_TOOL_SPEC,
        )
        return verdict

    async def judge_track_guess(
        self,
        *,
        locked_album_ctx: dict[str, Any],
        side_tracklist: list[dict[str, Any]],
        recent_history: list[dict[str, Any]],
        audible_up_iso: str | None,
        elapsed_since_audible_up_s: float,
        elapsed_since_last_confirm_s: float,
        predicted_position: str | None,
        likely_flip: bool = False,
        next_side_first: dict[str, Any] | None = None,
    ) -> Any:
        """Propose the most-likely playing track on a Shazam-miss +
        fingerprint-miss heartbeat when an album is locked.

        Called from the orchestrator's `_compute_track_guess` helper on
        the Shazam-miss + fingerprint-miss path. The LLM picks a
        `position` on the locked side; the orchestrator resolves
        `title` from the locked tracklist at publish time and emits a
        nested `guess` object on the WebSocket payload.

        Two separate elapsed-time fields (see
        docs/features/llm-track-guess-elapsed-frame-confusion/):
          - elapsed_since_audible_up_s: time since the most recent
            needle-drop (silent→audible edge). Doesn't reset on
            predicted-advance.
          - elapsed_since_last_confirm_s: time since the last positively
            anchored track (Shazam/fingerprint/user-pin/predicted).
            Resets on every confirmed track.

        Returns:
            TrackGuess(position, confidence, source, alt, reason) on success.
            USE_HEURISTIC when LLM disabled or any error path triggers.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_track_guess_prompt(
            locked_album_ctx=locked_album_ctx,
            side_tracklist=side_tracklist,
            recent_history=recent_history,
            audible_up_iso=audible_up_iso,
            elapsed_since_audible_up_s=elapsed_since_audible_up_s,
            elapsed_since_last_confirm_s=elapsed_since_last_confirm_s,
            predicted_position=predicted_position,
            likely_flip=likely_flip,
            next_side_first=next_side_first,
        )
        verdict = await self._invoke(
            "judge_track_guess",
            prompt,
            _parse_track_guess,
            tool_spec=_TRACK_GUESS_TOOL_SPEC,
        )
        return verdict

    async def decide_track_change(
        self,
        context: "dict[str, Any]",
    ) -> Any:
        """Primary track-change judge. Consulted on ambiguous heartbeats
        where deterministic Rule A would suppress a predicted advance
        (mid-track coverage gap).

        Called from `Orchestrator._handle_unmatched_music_level` after
        Rule A fires its suppression branch. Only reached when the key is
        set AND the deterministic rules are not confident; unambiguous
        Rule A decisions skip this call entirely.

        Returns:
            TrackChangeVerdict(decision, confidence, advance_to_position,
              reason) on success.
            USE_HEURISTIC when LLM disabled or any error path triggers.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_track_change_prompt(context)
        return await self._invoke(
            "decide_track_change",
            prompt,
            _parse_track_change,
            tool_spec=_TRACK_CHANGE_TOOL_SPEC,
        )

    async def clean_track_title(self, raw_title: str) -> Any:
        """Return the canonical track title (strip remaster/mix/year/edition
        annotations; keep performance variants). Used off the recognition
        hot path by the catalog title-cleaning passes.

        Returns:
            CleanTitle(clean_title) on success.
            USE_HEURISTIC when LLM disabled or any error path triggers.
        """
        if not self.enabled:
            return USE_HEURISTIC
        prompt = _build_clean_title_prompt(raw_title)
        return await self._invoke(
            "clean_track_title",
            prompt,
            CleanTitle,
            tool_spec=_CLEAN_TITLE_TOOL_SPEC,
        )

    # ── Internal plumbing ───────────────────────────────────────────────

    # F4 scaffold placeholder. Skylos correctly sees this as unused
    # today; F5–F8 each wire up their method's real prompt builder.
    # **Remove the inline suppression below once F5–F8 ship** and the
    # stub itself is either deleted or replaced by the per-method
    # builders. See docs/features/fingerprint-and-llm-assist/idea.md.
    @staticmethod
    def _stub_prompt(*args: Any) -> str:  # skylos: ignore — F4 scaffold placeholder; F5–F8 each replace this with a real per-method prompt builder; remove suppression once those ship
        """Deterministic stub prompt for F4. F5–F8 each replace their
        method's prompt-builder with the real domain-specific text."""
        return repr(args)

    def _cache_key(self, method_name: str, prompt: str) -> str:
        """Method-name-prefixed SHA256 key.

        The prefix prevents cross-method collisions when two hooks send
        structurally-similar prompts or empty stubs. Without it, e.g.
        `judge_advance(elapsed=0, ...)` and `rank_releases(empty, ...)`
        could collide and return each other's cached verdicts (with
        incompatible types).
        """
        return hashlib.sha256(f"{method_name}:{prompt}".encode()).hexdigest()

    def _cache_get(self, key: str) -> Any:
        entry = self._cache.get(key)
        if entry is not None:
            stored_at, value = entry
            if time.monotonic() - stored_at <= CACHE_TTL_S:
                return value
            self._cache.pop(key, None)
        return self._cache.get(key)

    def _cache_put(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic(), value)

    def _warn_throttled(self, exc: BaseException) -> None:
        """Log a warning at most once per exception class per
        ERROR_WARN_INTERVAL_S. Prevents heartbeat-cadence log spam when
        the API key is invalid or network is persistently down."""
        now = time.monotonic()
        last = self._last_warned.get(type(exc), 0.0)
        if now - last < ERROR_WARN_INTERVAL_S:
            return
        self._last_warned[type(exc)] = now
        log.warning("llm: %s call failed: %r (suppressing repeats for %.0fs)",
                    type(exc).__name__, exc, ERROR_WARN_INTERVAL_S)

    def _get_client(self) -> Any:
        """Lazily construct (and cache) the AsyncAnthropic client.

        Lazy + cached so the disabled path never imports `anthropic` and the
        enabled path doesn't re-establish the connection pool on every call.
        """
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "llm: ANTHROPIC_API_KEY is set but the `anthropic` SDK "
                "is not installed. Run `uv sync --extra llm` in pi/."
            ) from e
        self._client = anthropic.AsyncAnthropic(
            api_key=self._api_key, timeout=TIMEOUT_S,
        )
        return self._client

    async def _invoke(
        self,
        method_name: str,
        prompt: str,
        schema: type,
        tool_spec: dict | None = None,
    ) -> Any:
        """Cache → Anthropic SDK tool-use call → parse → return.

        When `tool_spec` is None (F6/F7/F8 stubs in their respective
        pre-impl states), the SDK client is constructed as a
        configuration-reachability smoke test and `USE_HEURISTIC` is
        returned — matching the F4 scaffold contract.

        When `tool_spec` is provided (F5 and beyond per hook), runs the
        full tool-use call, parses the response, caches the verdict, and
        returns `schema(**parsed_kwargs)`.

        Any exception → throttled warning + USE_HEURISTIC.
        """
        key = self._cache_key(method_name, prompt)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        try:
            verdict = await self._call_tool(prompt, schema, tool_spec)
        except Exception as e:
            self._warn_throttled(e)
            return USE_HEURISTIC

        if verdict is USE_HEURISTIC:
            return verdict
        self._cache_put(key, verdict)
        return verdict

    async def _call_tool(
        self,
        prompt: str,
        schema: type,
        tool_spec: dict | None,
    ) -> Any:
        """Execute one tool-use round-trip. Returns the parsed verdict, or
        `USE_HEURISTIC` when `tool_spec` is None (scaffold reachability
        check for not-yet-implemented hooks — constructs the client to
        prove configuration, then falls through)."""
        client = self._get_client()
        if tool_spec is None:
            _ = client
            return USE_HEURISTIC
        response = await client.messages.create(
            model=MODEL_ID,
            max_tokens=512,
            tools=[tool_spec],
            tool_choice={"type": "tool", "name": tool_spec["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
        parsed_input = _extract_tool_input(response, tool_spec["name"])
        return schema(**parsed_input)


def _extract_tool_input(response: Any, tool_name: str) -> dict:
    """Pull the structured tool-use block out of an Anthropic response.

    Defensive: raises ValueError on any shape mismatch (caller catches and
    falls back to USE_HEURISTIC). Tested via the malformed-response test.
    """
    content = getattr(response, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use" and getattr(block, "name", None) == tool_name:
            input_dict = getattr(block, "input", None)
            if isinstance(input_dict, dict):
                return input_dict
            raise ValueError(f"tool_use block.input is not a dict: {input_dict!r}")
    raise ValueError(f"no tool_use block named {tool_name!r} in response")
