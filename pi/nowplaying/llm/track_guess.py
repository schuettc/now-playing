"""Track-guess hook (confirm-first flow).

Verdict dataclass, Anthropic tool spec, prompt builder, and tool-input
parser for `LLMAssist.judge_track_guess`. The judge method itself lives
in `_assist.py`.
"""
from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class TrackGuess:
    """Output of `judge_track_guess`. The LLM picks a `position` and a
    confidence; `title` is resolved server-side from the locked tracklist
    at publish time (NOT supplied by the LLM, so we can't drift from the
    catalog). `alt` carries a second candidate ONLY for medium-confidence
    two-candidate cases — the prompt forbids alt for high (no ambiguity)
    or low (don't muddy a long-shot guess), and the parser drops `alt`
    post-LLM if confidence is anything but medium (defense in depth).
    `source` defaults to `"llm"` because the heuristic-fallback path
    builds the published dict directly without ever instantiating
    `TrackGuess`. `reason` is logged at INFO but NOT published.
    """

    position: str
    confidence: str  # "high" | "medium" | "low"
    source: str = "llm"
    alt: dict | None = None  # {"position": str} or None
    reason: str = ""


_TRACK_GUESS_TOOL_SPEC: dict = {
    "name": "judge_track_guess",
    "description": (
        "Propose the most-likely playing track on a vinyl record's locked "
        "side when Shazam missed and the local fingerprint DB had no match. "
        "Pick a `position` (e.g. 'A3') from the supplied side tracklist. "
        "Choose `confidence`: 'high' when the elapsed side time and recent "
        "history clearly point to one track; 'medium' when two adjacent "
        "tracks are plausible (set `alt` to the second candidate); 'low' "
        "when the guess is a long shot (do NOT set `alt`). Never set `alt` "
        "for high or low confidence. `alt` is for medium only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "position": {
                "type": "string",
                "description": (
                    "Track position (e.g. 'A3'). Normally from the supplied "
                    "side_tracklist (the locked side). When `likely_flip` "
                    "is true, the position SHOULD be the `next_side_first` "
                    "value from the prompt context — that's a different "
                    "side than the locked side, and that's correct. "
                    "Cumulative-numbered pressings (e.g. side B starts at "
                    "'B6' on some multi-LP albums) use whatever first-track "
                    "position the catalog reports for the next side."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": (
                    "Confidence level. UI variant selection depends on this. "
                    "Use 'medium' only when two adjacent tracks are plausible "
                    "candidates — set `alt` in that case."
                ),
            },
            "alt": {
                "type": ["object", "null"],
                "description": (
                    "Second candidate. Set ONLY when confidence is 'medium'. "
                    "Omit or set null for 'high' or 'low'."
                ),
                "properties": {
                    "position": {
                        "type": "string",
                        "description": (
                            "Second-candidate track position; must match a "
                            "position in the supplied tracklist."
                        ),
                    },
                },
                "required": ["position"],
            },
            "reason": {
                "type": "string",
                "description": (
                    "One short sentence justifying the guess. Logged "
                    "server-side at INFO; never shown to the user."
                ),
            },
        },
        "required": ["position", "confidence", "reason"],
    },
}


def _str(v: Any) -> Any:
    """Defensive stringifier: pass through None and primitive numerics
    (so JSON serialization preserves their type) and stringify anything
    else. Shared by the prompt-builder helpers below."""
    if v is None or isinstance(v, (int, float, bool)):
        return v
    return str(v)


def _norm_key(v: Any) -> str:
    """Casefold + strip for stable history-entry comparison."""
    return (v or "").strip().casefold() if isinstance(v, str) else ""


def _is_same_track(
    entry: dict[str, Any],
    cur_artist: str,
    cur_title: str,
) -> bool:
    """True iff `entry`'s (artist, title) matches the current locked
    track. Both current fields must be non-empty to count as a match —
    prevents an empty locked_ctx from dropping every history row."""
    if not (cur_artist and cur_title):
        return False
    return (
        _norm_key(entry.get("artist")) == cur_artist
        and _norm_key(entry.get("title")) == cur_title
    )


def _filter_recent_history(
    recent_history: list[dict[str, Any]] | None,
    locked_album_ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Drop the locked album's current `(artist, title)` from
    `recent_history` so the prompt hashes stably across heartbeats of
    the same track. Stripped to `{artist, title}` only — timestamps
    would change every call and bust the cache key."""
    cur_artist = _norm_key(locked_album_ctx.get("locked_artist"))
    cur_title = _norm_key(locked_album_ctx.get("locked_title"))
    return [
        {"artist": _str(h.get("artist")), "title": _str(h.get("title"))}
        for h in (recent_history or [])
        if not _is_same_track(h, cur_artist, cur_title)
    ]


def _tracklist_payload(
    side_tracklist: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize per-track dicts to
    ``{position, title, duration_s, start_s, end_s}``.

    ``start_s``/``end_s`` are the track's cumulative window in seconds from
    the side's start (in tracklist order), so the model can locate the
    playing track by a window lookup instead of summing durations itself —
    the summation it has historically gotten wrong (placing later tracks one
    full track too early). ``end_s`` is None when a track's duration is
    unknown (and the running total can't advance past it).

    Catalog key is ``duration_seconds``; falls back to legacy keys for test
    fixtures and future callers.
    """
    out: list[dict[str, Any]] = []
    cum = 0.0
    for t in side_tracklist:
        dur = (
            t.get("duration_seconds")
            or t.get("duration_s")
            or t.get("duration")
        )
        start_s = int(round(cum))
        if dur:
            cum += float(dur)
            end_s: int | None = int(round(cum))
        else:
            end_s = None
        out.append({
            "position": _str(t.get("track_position") or t.get("position")),
            "title": _str(t.get("title")),
            "duration_s": dur,
            "start_s": start_s,
            "end_s": end_s,
        })
    return out


def _locked_payload(locked_album_ctx: dict[str, Any]) -> dict[str, Any]:
    """Serialize the locked-album context block for the prompt."""
    return {
        "artist": _str(locked_album_ctx.get("locked_artist")),
        "album": _str(locked_album_ctx.get("locked_album")),
        "release_id": _str(locked_album_ctx.get("locked_release_id")),
        "side": _str(locked_album_ctx.get("locked_side")),
        "last_confirmed_track": _str(locked_album_ctx.get("locked_title")),
    }


def _bucket_elapsed(side_elapsed_s: float) -> int:
    """Bucket `side_elapsed_s` to the nearest 5s for cache stability.
    A 47s and 49s heartbeat both hash to the 50s bucket — same prompt,
    same cache key."""
    return int(round(side_elapsed_s / 5.0) * 5)


_TRACK_GUESS_PROMPT_HEADER = (
    "You are guessing which track on a vinyl record's locked side is "
    "playing right now. Shazam missed this heartbeat and the local "
    "fingerprint DB had no match, but we know the album. The kiosk "
    "will show your guess to the user as a confirmation prompt — not "
    "as ground truth. Pick the most likely position from the supplied "
    "tracklist.\n\n"
    "Each track lists `start_s`/`end_s` — its cumulative window in seconds "
    "from the side's start. An 'Estimated current position on the side' is "
    "provided. The playing track is normally the one whose [start_s, end_s) "
    "window contains that estimate — trust that lookup; do NOT re-derive it "
    "by summing durations yourself. Use recent history only to override for "
    "replays / skipped tracks, or to break a tie right at a track boundary.\n\n"
    "Confidence rules:\n"
    " - 'high': elapsed side time and recent history clearly point to "
    "one track. Do NOT set `alt`.\n"
    " - 'medium': two adjacent tracks are plausible (e.g., the needle "
    "could be on either side of a track boundary). Set `alt` to the "
    "second candidate.\n"
    " - 'low': long-shot guess (skipped track, lock-groove confusion, "
    "out-of-order play). Do NOT set `alt`.\n\n"
)


def _build_track_guess_prompt(
    *,
    locked_album_ctx: dict[str, Any],
    side_tracklist: list[dict[str, Any]],
    recent_history: list[dict[str, Any]],
    audible_up_iso: str | None,
    elapsed_since_audible_up_s: float,
    elapsed_since_last_confirm_s: float,
    predicted_position: str | None,
    estimated_side_position_s: float | None = None,
    likely_flip: bool = False,
    next_side_first: dict[str, Any] | None = None,
) -> str:
    """Render the prompt for `judge_track_guess`.

    Two separate elapsed fields with unambiguous names — see
    docs/features/llm-track-guess-elapsed-frame-confusion/ for the
    background. The single ``side_elapsed_s`` field that used to be
    here was actually measuring time-since-last-confirm and reset on
    every predicted-advance, which made the LLM repeatedly infer
    "side just started → A1/B1" whenever the orchestrator's
    state-decay path or predicted-advance refreshed the underlying
    anchor.

    Cache-stability invariants (mirrors `_build_promotion_prompt`):
      - Filter current (artist,title) out of recent_history — see
        `_filter_recent_history`.
      - Bucket both elapsed values to 5s — see `_bucket_elapsed`.
      - History entries stripped to `{artist, title}` only.
    """
    import json

    filtered_history = _filter_recent_history(recent_history, locked_album_ctx)
    bucketed_since_up = _bucket_elapsed(elapsed_since_audible_up_s)
    bucketed_since_confirm = _bucket_elapsed(elapsed_since_last_confirm_s)
    tracklist_payload = _tracklist_payload(side_tracklist)
    locked_payload = _locked_payload(locked_album_ctx)

    flip_block = _render_flip_block(likely_flip=likely_flip, next_side_first=next_side_first)

    return (
        _TRACK_GUESS_PROMPT_HEADER
        + f"Locked album: {json.dumps(locked_payload, separators=(',', ':'))}\n"
        + f"Side tracklist (in order): {json.dumps(tracklist_payload, separators=(',', ':'))}\n"
        + f"Audible-up timestamp: {json.dumps(audible_up_iso)}\n"
        + f"Elapsed since needle-drop / audible-up (5s bucket): "
        + f"{bucketed_since_up} seconds\n"
        + f"Elapsed since last confirmed track (5s bucket): "
        + f"{bucketed_since_confirm} seconds\n"
        + f"Heuristic prediction (predicted_position): {json.dumps(predicted_position)}\n"
        + (
            "Estimated current position on the side (seconds from side "
            "start, anchored to the confirmed track): "
            f"{_bucket_elapsed(estimated_side_position_s)}\n"
            if estimated_side_position_s is not None
            else ""
        )
        + f"Recent recognition history (excluding current track): "
        + f"{json.dumps(filtered_history, separators=(',', ':'))}\n"
        + flip_block
    )


def _render_flip_block(
    *,
    likely_flip: bool,
    next_side_first: dict[str, Any] | None,
) -> str:
    """Render the side-progression block when ``likely_flip`` is True.

    When True, tells the LLM the user almost certainly moved to the next
    side in the album's physical progression (since that's far more
    common than replaying the same side immediately) and provides the
    next side's first track. Also caps confidence at "medium" because
    progression-vs-restart can't be determined deterministically. When
    False, returns an empty string so the common case keeps a stable
    cache key.

    See docs/features/llm-track-guess-side-progression-not-flip/.
    """
    if not likely_flip or not next_side_first:
        return ""
    import json
    next_pos = next_side_first.get("position")
    next_title = next_side_first.get("title")
    next_side = next_side_first.get("side")
    next_payload = json.dumps(
        {"position": next_pos, "title": next_title, "side": next_side},
        separators=(",", ":"),
    )
    return (
        "likely_flip: true\n"
        "Side-progression guidance: the audible-up clock just reset AND "
        "the last confirmed track was deep into its side. The user almost "
        "certainly FLIPPED THE RECORD — moving to the NEXT SIDE in the "
        "album's progression is far more common than lifting and dropping "
        "the needle to replay the same side from the start.\n"
        f"Your `position` MUST be the NEXT SIDE's first track: {next_payload}.\n"
        "Only pick the locked side's opener (restart) if recent history "
        "shows the user has been replaying this side, OR if the catalog "
        "side mapping makes progression impossible.\n"
        "Confidence MUST be at most 'medium' on this heartbeat — physical "
        "progression-vs-restart cannot be determined deterministically "
        "until Shazam confirms a track on the new side.\n"
    )


def _parse_track_guess(**kwargs: Any) -> "TrackGuess":
    """Adapter from tool_use input shape to `TrackGuess`.

    Defense-in-depth: drop `alt` if confidence is anything but
    'medium' — the prompt forbids it but LLMs sometimes ignore prompt
    rules and the published payload shape rule should hold regardless.
    """
    position = str(kwargs.get("position", "")).strip()
    confidence = str(kwargs.get("confidence", "")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        raise ValueError(f"unexpected confidence: {confidence!r}")
    raw_alt = kwargs.get("alt")
    alt: dict | None = None
    if confidence == "medium" and isinstance(raw_alt, dict):
        alt_pos = raw_alt.get("position")
        if isinstance(alt_pos, str) and alt_pos.strip():
            alt = {"position": alt_pos.strip()}
    return TrackGuess(
        position=position,
        confidence=confidence,
        source="llm",
        alt=alt,
        reason=str(kwargs.get("reason", "")),
    )
