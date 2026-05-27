"""Release-picker hook (F7).

Verdict dataclass, Anthropic tool spec, prompt builder, and tool-input
parser for `LLMAssist.rank_releases`. The judge method itself lives in
`_assist.py`.
"""
from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class ReleaseRanking:
    """Output of `rank_releases`. LLM-preferred order of candidate
    Discogs release_ids; the first id is the top pick. F7 introduced
    the list form; the single-id form survives via the `release_id`
    property for any single-pick caller in F8."""

    release_ids: tuple[int, ...]
    reason: str = ""

    @property
    def release_id(self) -> int | None:
        """Top-pick release_id, or None if the LLM returned an empty list."""
        return self.release_ids[0] if self.release_ids else None


_RANK_RELEASES_TOOL_SPEC: dict = {
    "name": "rank_releases",
    "description": (
        "Re-rank Discogs release candidates by how well they match the "
        "user's search query, given any currently-locked album as context. "
        "Order matters; the kiosk shows results in the order returned. "
        "Prefer releases by the same artist as the locked context when "
        "ambiguous, prefer studio albums over compilations when the query "
        "suggests an album, and prefer canonical pressings over reissues "
        "unless year/label cues suggest otherwise."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ranked_release_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Discogs release_ids in LLM-preferred order. Include only "
                    "ids that appeared in the candidate list. Order matters."
                ),
            },
            "reason": {
                "type": "string",
                "description": "One short sentence justifying the ranking.",
            },
        },
        "required": ["ranked_release_ids", "reason"],
    },
}


def _build_rank_releases_prompt(  # skylos: ignore SKY-C304 — Why: 91 lines is the prompt template itself; extracting sub-sections would leave empty wrappers and make the prompt harder to read end-to-end
    candidates: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> str:
    """Render the prompt for `rank_releases` from candidates + context.

    Coaches the model with three signals beyond the raw candidate list:

    1. **Locked album by name** — pass ``locked_album_title`` in addition
       to ``locked_release_id``. Without the title the model has no way
       to identify the locked album in the candidate list by name; it
       was matching by opaque integer ID and inconsistently weighted
       the result against compilation prominence.
    2. **What just played** — pass ``locked_track_position`` and
       ``locked_track_title`` so the model knows what the user actually
       heard. If the user just heard "Something" on Abbey Road and is
       now searching "something", they're almost certainly disambiguating
       between Abbey Road and a compilation.
    3. **Per-candidate query-match flag** — compute
       ``has_matching_track`` from each candidate's tracklist. When the
       query matches a track on the locked album AND on other releases,
       the locked album is the answer.

    See docs/features/rank-releases-locked-album-priority/.
    """
    import json

    query_normed = (ctx.get("query") or "").lower().strip()

    def _track_matches_query(track_title: str | None) -> bool:
        """Loose match: query is a substring of the track title, or vice
        versa for short queries. Conservative — exact substring only,
        no fuzzy matching, no false-positive risk."""
        if not query_normed or not track_title:
            return False
        normed = track_title.lower().strip()
        if query_normed in normed or normed in query_normed:
            return True
        return False

    def _candidate(c: dict[str, Any]) -> dict[str, Any]:
        tracks = c.get("tracks") or []
        # Compute matching tracks (defensive — limit to top 3 matches
        # so the payload stays bounded for box sets / compilations).
        matching = []
        for t in tracks:
            if _track_matches_query(t.get("title")):
                matching.append({
                    "position": t.get("position") or t.get("track_position"),
                    "title": t.get("title"),
                })
                if len(matching) >= 3:
                    break
        return {
            "release_id": c.get("release_id"),
            "artist": c.get("artist"),
            "title": c.get("title"),
            "year": c.get("year"),
            "label": c.get("label"),
            "catno": c.get("catno"),
            "has_matching_track": bool(matching),
            "matching_tracks": matching,
        }

    candidates_payload = [_candidate(c) for c in candidates]
    return (
        "You are re-ranking Discogs release candidates for a vinyl-listening "
        "kiosk's /identify search results. The user is searching because "
        "they want to confirm what's on the turntable RIGHT NOW.\n\n"
        "Guidance:\n"
        "  - If the user is currently listening to a locked album AND the "
        "    query matches a track on that locked album (has_matching_track), "
        "    the locked album is the answer. They're disambiguating between "
        "    the pressing they own and other releases the track appears on.\n"
        "  - Prefer releases by the same artist as the locked album when "
        "    ambiguous.\n"
        "  - Prefer studio albums over compilations when the query suggests "
        "    an album (longer query, full track name).\n"
        "  - Prefer canonical pressings over reissues unless year/label cues "
        "    say otherwise.\n"
        "  - Candidates without has_matching_track (the query doesn't appear "
        "    in their tracklist) are weak matches — rank them last.\n\n"
        f"User query: {json.dumps(ctx.get('query'))}\n"
        f"Locked release_id: {json.dumps(ctx.get('locked_release_id'))}\n"
        f"Locked album title: {json.dumps(ctx.get('locked_album_title'))}\n"
        f"Locked artist: {json.dumps(ctx.get('locked_artist'))}\n"
        f"Just-played track on locked album: "
        f"{json.dumps(ctx.get('locked_track_position'))} "
        f"{json.dumps(ctx.get('locked_track_title'))}\n"
        f"Candidates: {json.dumps(candidates_payload, separators=(',', ':'))}\n"
    )


def _parse_release_ranking(**kwargs: Any) -> "ReleaseRanking":
    """Adapter from tool_use input shape (`ranked_release_ids: list[int]`)
    to the `ReleaseRanking` dataclass (`release_ids: tuple[int, ...]`)."""
    raw_ids = kwargs.get("ranked_release_ids") or []
    cleaned: list[int] = []
    for x in raw_ids:
        try:
            cleaned.append(int(x))
        except (TypeError, ValueError):
            continue
    return ReleaseRanking(
        release_ids=tuple(cleaned),
        reason=str(kwargs.get("reason", "")),
    )


# ─── judge_reverse_lookup (record-flip-aware disambiguation) ─────────────


@dataclasses.dataclass(frozen=True)
class ReleasePick:
    """Output of `judge_reverse_lookup`. Picks ONE release_id from a
    heuristic-tied candidate set (winner + alternates). Distinct from
    `ReleaseRanking` which returns an ordering — reverse-lookup only
    needs the answer."""

    release_id: int
    reason: str = ""


_REVERSE_LOOKUP_TOOL_SPEC: dict = {
    "name": "judge_reverse_lookup",
    "description": (
        "A Shazam recognition just confirmed an (artist, title) pair. "
        "The catalog's heuristic scoring found a winning release plus "
        "alternates with similar scores — the result is ambiguous and "
        "needs disambiguation, especially when the user may have just "
        "flipped to a different record. Use the recent-play history and "
        "locked-album context to pick which release_id is most likely "
        "the user's actual pressing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "release_id": {
                "type": "integer",
                "description": (
                    "The release_id that best matches the user's pressing. "
                    "Must be one of the release_ids from the winner or "
                    "alternates list."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "One short sentence justifying the pick — call out the "
                    "evidence (recent history, time gap, locked album) that "
                    "drove the decision."
                ),
            },
        },
        "required": ["release_id", "reason"],
    },
}


def _bucket_seconds(s: float | int | None, bucket: int = 5) -> int:
    """Round seconds to a fixed bucket so prompts hash identically across
    nearby heartbeats. Mirrors `_bucket_elapsed` in track_guess.py."""
    if s is None:
        return 0
    try:
        return int(round(float(s) / float(bucket)) * bucket)
    except (TypeError, ValueError):
        return 0


def _build_reverse_lookup_prompt(  # skylos: ignore SKY-C304 — Why: 81 lines is the prompt template itself; the length comes from multi-section prompt scaffolding that must stay co-located to be reviewable
    winner: dict[str, Any],
    alternates: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> str:
    """Render the prompt for `judge_reverse_lookup`.

    Cache-stability invariants:
      - Bucket `seconds_since_last_confirm` to 5s.
      - Recent history entries stripped to `{artist, title, release_id}`
        (drop wall-clock timestamps).
      - Candidate keys sorted deterministically in the JSON payload.
    """
    import json

    def _candidate_payload(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "release_id": c.get("release_id"),
            "artist": c.get("artist"),
            "album": c.get("album") or c.get("title"),
            "year": c.get("year"),
            "format": c.get("format"),
            "matched_track_position": (
                c.get("matched_track_position") or c.get("track_position")
            ),
            "matched_track_title": (
                c.get("matched_track_title") or c.get("track_title")
            ),
            "score": c.get("score") or c.get("match_score"),
        }

    candidates_payload = [_candidate_payload(winner)] + [
        _candidate_payload(a) for a in alternates
    ]

    history_payload = [
        {
            "artist": e.get("artist"),
            "title": e.get("title"),
            "release_id": e.get("release_id"),
        }
        for e in (ctx.get("recent_history") or [])
    ]

    bucketed_since_confirm = _bucket_seconds(
        ctx.get("seconds_since_last_confirm"),
    )

    return (
        "You are disambiguating a Shazam recognition for a vinyl-listening "
        "kiosk. The catalog's heuristic scoring found a winning release "
        "with one or more alternates whose scores are within ~20 points — "
        "this means the heuristic itself flagged the match as ambiguous.\n\n"
        "Your job is to pick the right release_id, especially when the user "
        "may have just FLIPPED to a different record. Signals that suggest "
        "a flip:\n"
        "  - The locked album in context no longer matches recent history "
        "    (suggests the user changed records).\n"
        "  - A large time gap since the last confirmed track (suggests the "
        "    needle was lifted to change sides or albums).\n"
        "  - Recent history is dense on one album but the Shazam result "
        "    matches a different release with similar audio.\n\n"
        "Default to the locked album when recent history is consistent and "
        "the time gap is small. Switch to an alternate when the flip "
        "signals point to a new record.\n\n"
        f"Shazam result: artist={json.dumps(ctx.get('query_artist'))} "
        f"title={json.dumps(ctx.get('query_title'))} "
        f"isrc={json.dumps(ctx.get('query_isrc'))}\n"
        f"Locked album: release_id={json.dumps(ctx.get('locked_release_id'))} "
        f"title={json.dumps(ctx.get('locked_album_title'))} "
        f"artist={json.dumps(ctx.get('locked_artist'))}\n"
        f"Just-played track on locked album: "
        f"{json.dumps(ctx.get('locked_track_position'))} "
        f"{json.dumps(ctx.get('locked_track_title'))}\n"
        f"Seconds since last confirmed track (5s bucket): "
        f"{bucketed_since_confirm}\n"
        f"Recent history (most recent first): "
        f"{json.dumps(history_payload, separators=(',', ':'))}\n"
        f"Candidates (winner first): "
        f"{json.dumps(candidates_payload, separators=(',', ':'))}\n"
    )


def _parse_release_pick(**kwargs: Any) -> "ReleasePick":
    """Adapter from tool_use input shape to ``ReleasePick``."""
    try:
        rid = int(kwargs.get("release_id"))
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"judge_reverse_lookup: invalid release_id {kwargs.get('release_id')!r}: {e}",
        ) from e
    return ReleasePick(release_id=rid, reason=str(kwargs.get("reason", "")))
