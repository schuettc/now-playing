"""Optional LLM-assisted decision points (Anthropic Haiku 4.5).

When `ANTHROPIC_API_KEY` is set in `pi/.env`, replaces hardcoded
heuristics at four cascade decision points with LLM judgments:

  - `judge_shazam_result`   — cover/tribute/sample relevance filter
  - `judge_advance`         — end-of-side advance under tracklist-aware fallback
  - `rank_releases`         — /identify Discogs release ranker

When the key is unset, every method returns the `USE_HEURISTIC`
sentinel and the orchestrator uses today's hardcoded logic. The
`anthropic` SDK is **lazy-imported** inside `_get_client` so users
without the `[llm]` extra never pay an import cost.

This module is the F4 scaffold: all four public methods exist and
are wired to a fully-exercised `_invoke` path (cache, timeout, error
fallback). F5–F8 each fill in real prompts/schemas in their own
sibling module.

See `docs/features/fingerprint-and-llm-assist/idea.md` for the
umbrella design and `docs/features/llm-assist-scaffold/idea.md` for
this slice.
"""
from __future__ import annotations

from nowplaying.llm._assist import (
    CACHE_TTL_S,
    ERROR_WARN_INTERVAL_S,
    LLMAssist,
    MODEL_ID,
    TIMEOUT_S,
    USE_HEURISTIC,
    _extract_tool_input,
    log,
)
from nowplaying.llm.advance import (
    AdvanceVerdict,
    _ADVANCE_TOOL_SPEC,
    _build_advance_prompt,
)
from nowplaying.llm.release import (
    ReleaseRanking,
    _RANK_RELEASES_TOOL_SPEC,
    _build_rank_releases_prompt,
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

__all__ = [
    "AdvanceVerdict",
    "CACHE_TTL_S",
    "ERROR_WARN_INTERVAL_S",
    "LLMAssist",
    "MODEL_ID",
    "ReleaseRanking",
    "ShazamVerdict",
    "TIMEOUT_S",
    "TrackChangeVerdict",
    "USE_HEURISTIC",
    "log",
    "_ADVANCE_TOOL_SPEC",
    "_RANK_RELEASES_TOOL_SPEC",
    "_SHAZAM_TOOL_SPEC",
    "_TRACK_CHANGE_TOOL_SPEC",
    "_build_advance_prompt",
    "_build_rank_releases_prompt",
    "_build_shazam_prompt",
    "_build_track_change_prompt",
    "_extract_tool_input",
    "_parse_release_ranking",
    "_parse_track_change",
]
