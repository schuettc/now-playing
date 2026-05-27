"""Shazam-relevance hook (F5).

Verdict dataclass, Anthropic tool spec, and prompt builder for
`LLMAssist.judge_shazam_result`. The judge method itself lives in
`_assist.py` to keep `LLMAssist` cohesive.
"""
from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class ShazamVerdict:
    """Output of `judge_shazam_result`. F5 fills in semantics."""

    accept: bool
    reason: str = ""


_SHAZAM_TOOL_SPEC: dict = {
    "name": "judge_shazam_result",
    "description": (
        "Decide whether a Shazam recognition result is a real new-record "
        "signal or noise (cover, sample, mis-ID, brief overlap with a "
        "different release). Called only when the Shazam result disagrees "
        "with the currently-locked album context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "accept": {
                "type": "boolean",
                "description": (
                    "True if the Shazam result represents a real new-record "
                    "drop (orchestrator should publish it and reset the "
                    "album lock). False if it's noise (orchestrator should "
                    "swallow the heartbeat and keep the existing lock)."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "One short sentence explaining the verdict, surfaced in "
                    "the orchestrator's INFO log."
                ),
            },
        },
        "required": ["accept", "reason"],
    },
}


def _build_shazam_prompt(
    shazam_track: dict[str, Any],
    locked_album_ctx: dict[str, Any] | None,
) -> str:
    """Render the prompt for `judge_shazam_result` from raw recognizer
    output + locked-album context. Fields are stringified defensively;
    missing fields render as null."""
    def _str(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool)):
            return v
        return str(v)

    shazam_payload = {
        "artist": _str(shazam_track.get("artist")),
        "title": _str(shazam_track.get("title")),
        "album": _str(shazam_track.get("album")),
        "year": _str(shazam_track.get("year")),
        "isrc": _str(shazam_track.get("isrc")),
        "release_id": _str(shazam_track.get("release_id")),
    }
    if locked_album_ctx is None:
        locked_payload: dict[str, Any] = {"locked": False}
    else:
        locked_payload = {
            "locked": True,
            "locked_artist": _str(locked_album_ctx.get("locked_artist")),
            "locked_album": _str(locked_album_ctx.get("locked_album")),
            "locked_release_id": _str(locked_album_ctx.get("locked_release_id")),
            "locked_title": _str(locked_album_ctx.get("locked_title")),
        }

    import json
    return (
        "You are gating Shazam recognition results for a vinyl-listening kiosk.\n"
        "Decide whether the new Shazam hit represents a real record change or "
        "is noise (a cover, sample, or brief overlap from a different release).\n"
        "Choose `accept: true` only when the evidence supports a real new-record "
        "drop. When uncertain, prefer `accept: false` so the kiosk keeps the "
        "current album rather than flipping.\n\n"
        f"Shazam result: {json.dumps(shazam_payload, separators=(',', ':'))}\n"
        f"Locked context: {json.dumps(locked_payload, separators=(',', ':'))}\n"
    )
