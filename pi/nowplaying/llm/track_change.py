"""Track-change primary judge (LLM-primary, Rule A fallback).

Verdict dataclass, Anthropic tool spec, prompt builder, and tool-input
parser for `LLMAssist.decide_track_change`. The judge method itself lives
in `_assist.py`.

Consulted when `ANTHROPIC_API_KEY` is set AND the deterministic Rule A
duration guard would suppress a predicted-advance (ambiguous mid-track
coverage gap). On unambiguous deterministic decisions no LLM call is made.

Response shape: {decision: "hold"|"advance"|"uncertain", confidence: 0..1,
advance_to_position?: str, reason: str}

Sanity check: advance_to_position MUST exist on the locked release tracklist;
if not, the caller downgrades to hold. See docs/features/llm-track-change-primary/.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any


@dataclasses.dataclass(frozen=True)
class TrackChangeVerdict:
    """Output of `decide_track_change`.

    `decision` is "hold", "advance", or "uncertain".
    `confidence` is 0.0..1.0 — callers treat < 0.7 as "not confident enough
    to override Rule A".
    `advance_to_position` is present only when decision == "advance"; the
    orchestrator validates it against the locked tracklist before acting.
    `reason` is logged at INFO but NOT published to the kiosk.
    """

    decision: str  # "hold" | "advance" | "uncertain"
    confidence: float  # 0.0 .. 1.0
    advance_to_position: str | None = None
    reason: str = ""


_TRACK_CHANGE_TOOL_SPEC: dict = {
    "name": "decide_track_change",
    "description": (
        "Decide whether the vinyl kiosk should hold on the current locked "
        "track or advance to a different position on the same release. "
        "You are called when deterministic duration-guard rules are ambiguous "
        "(N-misses mid-track, not near end-of-track). "
        "Return 'hold' when the evidence supports staying on the current track. "
        "Return 'advance' with a valid `advance_to_position` when the evidence "
        "clearly points to a different track being audible right now — e.g. "
        "fingerprint hits on a different position, Shazam (even if gated) "
        "identifying a different track, or audible energy transitions. "
        "Return 'uncertain' when the evidence is genuinely ambiguous and you "
        "cannot choose confidently. "
        "Set `confidence` to 0.0..1.0 reflecting how sure you are. "
        "Only return 'advance' when confidence >= 0.7 and you have a specific "
        "position to advance to. "
        "`advance_to_position` MUST be a position that exists in the supplied "
        "tracklist (e.g. 'A2'). Never invent a position."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["hold", "advance", "uncertain"],
                "description": (
                    "'hold' = stay on current track. "
                    "'advance' = move to advance_to_position. "
                    "'uncertain' = not enough evidence to decide."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in the decision, 0.0 (no idea) to 1.0 (certain).",
            },
            "advance_to_position": {
                "type": ["string", "null"],
                "description": (
                    "Target track position (e.g. 'A2') when decision is 'advance'. "
                    "Must exist in the supplied tracklist. Omit or null for hold/uncertain."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "One short sentence justifying the decision. Logged server-side "
                    "at INFO; never shown to the user."
                ),
            },
        },
        "required": ["decision", "confidence", "reason"],
    },
}

_TRACK_CHANGE_PROMPT_HEADER = (
    "You are the track-change judge for a vinyl record kiosk. "
    "A deterministic rule suppressed an automatic track advance because "
    "elapsed time is well short of the locked track's full duration — "
    "this looks like a mid-track recognition coverage gap rather than a "
    "real track change. But there may be additional evidence that "
    "something different is playing.\n\n"
    "Your job: weigh all the evidence below and return hold, advance, or "
    "uncertain. Only return advance when you have a specific position to "
    "move to AND confidence >= 0.7.\n\n"
)


def _build_track_change_prompt(context: dict[str, Any]) -> str:
    """Render the prompt for `decide_track_change`.

    `context` keys (all optional — missing keys degrade gracefully):
      locked_track: {release_id, title, position, duration_s}
      elapsed_since_last_signal_s: float
      recent_fp_hits: [{position, hits}] (last N heartbeats)
      last_shazam_gated: {artist, title, release_id (nullable), confidence (nullable)} | None
      recent_audible_edges: [{type: "audible"|"silent", ts_iso: str}]
      full_tracklist: [{position, title, duration_s}]
    """
    locked = context.get("locked_track") or {}
    elapsed = context.get("elapsed_since_last_signal_s")
    fp_hits = context.get("recent_fp_hits") or []
    shazam = context.get("last_shazam_gated")
    edges = context.get("recent_audible_edges") or []
    tracklist = context.get("full_tracklist") or []

    parts = [_TRACK_CHANGE_PROMPT_HEADER]

    parts.append(
        f"Locked track: {json.dumps(locked, separators=(',', ':'))}\n"
    )
    parts.append(
        f"Elapsed since last confident recognition signal (seconds): "
        f"{json.dumps(elapsed)}\n"
    )
    parts.append(
        f"Recent fingerprint hit history (last heartbeats, position→hits): "
        f"{json.dumps(fp_hits, separators=(',', ':'))}\n"
    )
    parts.append(
        f"Most recent Shazam result (may be gated/low-confidence): "
        f"{json.dumps(shazam, separators=(',', ':'))}\n"
    )
    parts.append(
        f"Recent audible/silent edge events (last 60s): "
        f"{json.dumps(edges, separators=(',', ':'))}\n"
    )
    parts.append(
        f"Full tracklist for this release (valid advance_to_position values): "
        f"{json.dumps(tracklist, separators=(',', ':'))}\n"
    )

    return "".join(parts)


def _parse_track_change(**kwargs: Any) -> TrackChangeVerdict:
    """Parse tool-use input into a `TrackChangeVerdict`.

    Defense-in-depth:
    - decision must be one of the three valid values.
    - confidence is clamped to [0.0, 1.0].
    - advance_to_position is only preserved when decision == "advance".
    """
    decision = str(kwargs.get("decision", "")).strip().lower()
    if decision not in {"hold", "advance", "uncertain"}:
        raise ValueError(f"unexpected decision: {decision!r}")

    raw_conf = kwargs.get("confidence", 0.0)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        raise ValueError(f"non-numeric confidence: {raw_conf!r}")
    confidence = max(0.0, min(1.0, confidence))

    advance_to_position: str | None = None
    if decision == "advance":
        raw_pos = kwargs.get("advance_to_position")
        if isinstance(raw_pos, str) and raw_pos.strip():
            advance_to_position = raw_pos.strip()

    return TrackChangeVerdict(
        decision=decision,
        confidence=confidence,
        advance_to_position=advance_to_position,
        reason=str(kwargs.get("reason", "")),
    )
