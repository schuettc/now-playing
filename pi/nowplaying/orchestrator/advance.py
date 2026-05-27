"""LLM-advance verdict translation + elapsed-since-anchor math."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("nowplaying.main")


def _compute_advance_elapsed_s(
    track_started_at: str | None, seed_back_s: float,
) -> float:
    """Seconds since the last confirmed track-start anchor, falling back
    to ``seed_back_s`` when the anchor is missing or unparseable.
    """
    if not track_started_at:
        return float(seed_back_s)
    try:
        anchor = datetime.fromisoformat(
            track_started_at.replace("Z", "+00:00"),
        )
    except (ValueError, AttributeError) as e:
        log.warning(
            "advance-track: unparseable track_started_at=%r (%s); "
            "falling back to seed_back_s",
            track_started_at, e,
        )
        return float(seed_back_s)
    return (datetime.now(timezone.utc) - anchor).total_seconds()


def _interpret_advance_verdict(
    verdict, side_tracklist: list[dict], last_title,
) -> str | None:
    """Translate an LLM advance verdict into the orchestrator's
    `"STAY" | track_position | None` contract. ``verdict`` may be the
    USE_HEURISTIC sentinel or an `AdvanceVerdict`; the sentinel and
    out-of-range indices map to None (heuristic).
    """
    from nowplaying.llm import USE_HEURISTIC
    if verdict is USE_HEURISTIC:
        return None
    if verdict.advance_to_index is None:
        log.info(
            "advance-track: LLM says stay on %r — %s",
            last_title, verdict.reason,
        )
        return "STAY"
    idx = verdict.advance_to_index
    if not (0 <= idx < len(side_tracklist)):
        log.warning(
            "advance-track: LLM returned out-of-range index=%d "
            "(side has %d tracks); falling back to heuristic",
            idx, len(side_tracklist),
        )
        return None
    target = side_tracklist[idx].get("track_position")
    log.info(
        "advance-track: LLM advances to index=%d position=%s — %s",
        idx, target, verdict.reason,
    )
    return target
