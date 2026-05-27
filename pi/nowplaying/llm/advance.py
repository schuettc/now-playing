"""Advance-track hook (F6).

Verdict dataclass, Anthropic tool spec, and prompt builder for
`LLMAssist.judge_advance`. The judge method itself lives in `_assist.py`.
"""
from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class AdvanceVerdict:
    """Output of `judge_advance`. F6 fills in semantics."""

    advance_to_index: int | None
    reason: str = ""


_ADVANCE_TOOL_SPEC: dict = {
    "name": "judge_advance",
    "description": (
        "Decide which track on the locked album side the needle is on "
        "now, given the elapsed time since the last confirmed track and "
        "the ordered tracklist of the side. Return null for "
        "advance_to_index if the needle is still on the last confirmed "
        "track."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "advance_to_index": {
                "type": ["integer", "null"],
                "description": (
                    "Zero-based index into the side tracklist of the track "
                    "the needle is on now. Use null to indicate the needle "
                    "is still on the last confirmed track."
                ),
            },
            "reason": {
                "type": "string",
                "description": "One short sentence justifying the verdict.",
            },
        },
        "required": ["advance_to_index", "reason"],
    },
}


def _build_advance_prompt(
    elapsed_s: float,
    last_track: dict[str, Any] | None,
    side_tracklist: list[dict[str, Any]],
) -> str:
    """Render the prompt for `judge_advance` from elapsed time + tracklist."""
    import json

    def _track_summary(t: dict[str, Any], idx: int) -> dict[str, Any]:
        return {
            "index": idx,
            "track_position": t.get("track_position") or t.get("position"),
            "title": t.get("title"),
            # Catalog key is `duration_seconds`; fall back to legacy keys.
            "duration_s": (
                t.get("duration_seconds")
                or t.get("duration_s")
                or t.get("duration")
            ),
        }

    tracklist_payload = [
        _track_summary(t, i) for i, t in enumerate(side_tracklist)
    ]
    last_payload = (
        None if last_track is None else {
            "title": last_track.get("title"),
            "track_position": last_track.get("track_position"),
            "side": last_track.get("side"),
        }
    )
    return (
        "You are deciding which track on a vinyl record side the needle is "
        "on right now, given that recognition has missed several heartbeats.\n"
        "Decide arithmetically from the elapsed time and the tracklist:\n"
        "  - If the last track has a non-null `duration_s` and `elapsed_s` "
        "    EXCEEDS that duration (with a few seconds of grace for clock "
        "    skew), the needle has moved past it — advance to the next "
        "    track in the tracklist (or further if elapsed exceeds the "
        "    cumulative duration of multiple tracks).\n"
        "  - If `elapsed_s` is shorter than the last track's `duration_s`, "
        "    the needle is still on that track — return null.\n"
        "  - If the last track's `duration_s` is null (unknown), default "
        "    to staying (return null) unless `elapsed_s` is implausibly "
        "    large vs. typical vinyl track length (~3 minutes).\n"
        "Do NOT hedge against the arithmetic when durations are known. "
        "If elapsed > duration, advance — even by 1-2 seconds margin.\n\n"
        f"Elapsed seconds since track start: {elapsed_s:.0f}\n"
        f"Last confirmed track: {json.dumps(last_payload, separators=(',', ':'))}\n"
        f"Side tracklist (in order): {json.dumps(tracklist_payload, separators=(',', ':'))}\n"
    )
