"""GET /api/collection/search — tokenized score-ranked local catalog search."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from nowplaying.discogs import catalog as discogs_catalog

from nowplaying.control._shared import log


def _build_search_sql(tokens: list[str]) -> tuple[str, list[str]]:
    """Build a parameterized WHERE clause that ANDs one OR-group per token.

    Each token must match somewhere across artist / title / catno / track
    title. Returns the full SELECT statement plus the bind-parameter list.
    Limit is intentionally larger than the response cap so the Python
    re-ranker has candidates to score and re-sort.
    """
    where_parts: list[str] = []
    binds: list[str] = []
    for tok in tokens:
        like = f"%{tok}%"
        where_parts.append(
            "(artist LIKE ? OR title LIKE ? OR catno LIKE ?"
            " OR id IN (SELECT release_id FROM tracks WHERE title LIKE ?))"
        )
        binds.extend([like, like, like, like])
    where_clause = " AND ".join(where_parts) if where_parts else "1=1"
    sql = (
        "SELECT id, artist, title, year, label, catno "
        "FROM releases "
        f"WHERE {where_clause} "
        "LIMIT 50"
    )
    return sql, binds


def _score_token(
    tok: str, artist_lc: str, title_lc: str, catno_lc: str, tracks_lc: list[str],
) -> int:
    """Weighted per-token score: artist > title > track-title = catno."""
    sub = 0
    if tok in artist_lc:
        sub += 3
    if tok in title_lc:
        sub += 2
    if any(tok in t for t in tracks_lc):
        sub += 1
    if tok in catno_lc:
        sub += 1
    return sub


def _score_release(row: dict, track_titles: list[str], tokens: list[str]) -> int:
    artist_lc = (row.get("artist") or "").lower()
    title_lc = (row.get("title") or "").lower()
    catno_lc = (row.get("catno") or "").lower()
    tracks_lc = [(t or "").lower() for t in track_titles]
    return sum(
        _score_token(tok, artist_lc, title_lc, catno_lc, tracks_lc) for tok in tokens
    )


def _fetch_release_tracks(con, rid: int) -> list[dict]:
    """Fetch ordered track rows for a release in the search-result shape."""
    return [
        {
            "position": t["position"],
            "side": t["side"],
            "title": t["title"],
            "duration_seconds": t["duration_seconds"],
            "clean_title": t["clean_title"],
        }
        for t in con.execute(
            "SELECT position, side, title, duration_seconds, clean_title FROM tracks WHERE release_id = ? ORDER BY position",
            (rid,),
        )
    ]


def _build_search_item(r, tracks: list[dict], tokens_lc: list[str]) -> dict:
    """Assemble a single scored search-result item from a releases row + its tracks."""
    item = {
        "release_id": r["id"],
        "artist": r["artist"],
        "title": r["title"],
        "year": r["year"],
        "label": r["label"],
        "catno": r["catno"],
        "tracks": tracks,
    }
    item["_score"] = _score_release(item, [t["title"] for t in tracks], tokens_lc)
    return item


def _collect_search_items(tokens: list[str], tokens_lc: list[str]) -> list[dict]:
    """Run the parameterized search and assemble scored items."""
    sql, binds = _build_search_sql(tokens)
    items: list[dict] = []
    with discogs_catalog.open_ro() as con:
        rows = con.execute(sql, binds).fetchall()
        for r in rows:
            tracks = _fetch_release_tracks(con, r["id"])
            items.append(_build_search_item(r, tracks, tokens_lc))
    return items


def _rank_and_truncate(items: list[dict], limit: int = 20) -> list[dict]:
    """Sort items by score desc / artist / title and truncate to limit."""
    items.sort(key=lambda x: (
        -x["_score"],
        (x.get("artist") or "").lower(),
        (x.get("title") or "").lower(),
    ))
    return items[:limit]


def _build_artist_groups(items: list[dict]) -> list[dict]:
    """Group items by artist; keep only artists with 3+ releases in the truncated set."""
    by_artist: dict[str, list[dict]] = {}
    for it in items:
        by_artist.setdefault(it.get("artist") or "", []).append(it)
    groups = [
        {"artist": artist, "releases": rels}
        for artist, rels in by_artist.items()
        if artist and len(rels) >= 3
    ]
    groups.sort(key=lambda g: (-len(g["releases"]), g["artist"].lower()))
    return groups


def _strip_scores(items: list[dict], groups: list[dict]) -> None:
    """Remove the internal _score key from items and grouped releases in-place."""
    for it in items:
        it.pop("_score", None)
    for g in groups:
        for it in g["releases"]:
            it.pop("_score", None)


def _build_rerank_ctx(query: str, state: Any) -> dict:
    """Pure: extract locked-album context into the LLM ctx dict.
    state may be None (no state wired) or have last_vinyl=None
    (vinyl recognition idle); both yield a query-only context.

    Includes the locked album's *title* (not just release_id) so the
    model can reason about candidates by name rather than by opaque ID,
    plus the most-recently-confirmed track position/title so the model
    knows what the user just heard. See
    docs/features/rank-releases-locked-album-priority/.
    """
    locked = getattr(state, "last_vinyl", None) if state is not None else None
    if not locked:
        return {
            "query": query,
            "locked_release_id": None,
            "locked_artist": None,
            "locked_album_title": None,
            "locked_track_position": None,
            "locked_track_title": None,
        }
    return {
        "query": query,
        "locked_release_id": locked.get("release_id"),
        "locked_artist": locked.get("artist"),
        "locked_album_title": locked.get("album"),
        "locked_track_position": locked.get("track_position"),
        "locked_track_title": locked.get("title"),
    }


def _reorder_by_verdict(
    candidates: list[dict], release_ids: list[int],
) -> tuple[list[dict], int] | None:
    """Reorder candidates to match the LLM's release_ids ordering,
    appending any unmentioned candidates in their original order.
    Returns (reordered, n_matched) or None when the LLM verdict
    contained no recognized release_ids (hallucination — caller
    falls back to the heuristic order)."""
    candidate_index: dict[int, dict] = {c["release_id"]: c for c in candidates}
    reordered: list[dict] = []
    seen: set[int] = set()
    for rid in release_ids:
        item = candidate_index.get(rid)
        if item is not None and rid not in seen:
            reordered.append(item)
            seen.add(rid)
    if not reordered:
        return None
    for c in candidates:
        if c["release_id"] not in seen:
            reordered.append(c)
    return reordered, len(seen)


async def _maybe_llm_rerank_items(
    request: web.Request, query: str, items: list[dict],
) -> list[dict]:
    """F7 LLM release-picker hook: re-rank the top-N items contextually.

    Gates:
      - LLM disabled or not wired on app → pass through.
      - Fewer than 2 items → nothing to re-rank.
    Re-ranks the top 10. Trailing items keep their heuristic order at the
    end. Defensively skips any release_ids returned by the LLM that don't
    appear in the heuristic candidate set; if no valid ids remain, leaves
    the heuristic order unchanged.
    """
    llm = request.app.get("llm")
    if llm is None or not getattr(llm, "enabled", False) or len(items) < 2:
        return items
    from nowplaying.llm import USE_HEURISTIC
    top_n = 10
    candidates = items[:top_n]
    ctx = _build_rerank_ctx(query, request.app.get("state"))
    try:
        verdict = await llm.rank_releases(candidates, ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("release-picker: rank_releases raised %r; using heuristic", e)
        return items
    if verdict is USE_HEURISTIC:
        return items
    result = _reorder_by_verdict(candidates, verdict.release_ids)
    if result is None:
        return items  # LLM hallucinated entirely
    reordered, n_matched = result
    reordered.extend(items[top_n:])
    log.info(
        "release-picker: reordered %d of %d by LLM — %s",
        n_matched, len(candidates), verdict.reason,
    )
    return reordered


async def release_tracklist(request: web.Request) -> web.Response:
    """GET /api/release/{release_id}/tracklist — ordered tracklist for a catalog release.

    Returns tracks from the local Discogs catalog SQLite DB. This endpoint
    exists so the kiosk can show a past album's tracklist without requiring
    that album to be currently playing (the WebSocket payload only carries
    the currently-locked release's tracks).

    Returns 200 `{"ok": true, "tracks": [...]}` on success.
    Returns 404 if `release_id` is not in the catalog.
    Returns 400 if `release_id` is not a valid integer.
    """
    raw = request.match_info.get("release_id", "")
    try:
        rid = int(raw)
    except (ValueError, TypeError):
        return web.json_response({"ok": False, "error": "release_id must be an integer"}, status=400)

    try:
        with discogs_catalog.open_ro() as con:
            # Verify the release exists before fetching tracks
            row = con.execute("SELECT id FROM releases WHERE id = ?", (rid,)).fetchone()
            if row is None:
                return web.json_response({"ok": False, "error": "release not found"}, status=404)
            tracks = _fetch_release_tracks(con, rid)
    except Exception as e:  # noqa: BLE001
        log.warning("release_tracklist: error fetching rid=%s: %r", rid, e)
        return web.json_response({"ok": False, "error": "internal error"}, status=500)

    return web.json_response({"ok": True, "tracks": tracks})


async def search_collection(request: web.Request) -> web.Response:
    """Tokenized, score-ranked search across the local Discogs collection.

    Splits the query into whitespace tokens and requires each token to hit
    at least one of (artist, title, catno, track title). Re-ranks the
    candidate pool in Python by a weighted score (artist > title > track
    > catno), truncates to 20, and surfaces artists with 3+ hits as
    artist groups alongside the flat list.
    """
    q = (request.query.get("q") or "").strip()
    if not q or len(q) < 2:
        return web.json_response({"ok": True, "groups": [], "items": []})
    tokens = [t for t in q.split() if t]
    if not tokens:
        return web.json_response({"ok": True, "groups": [], "items": []})
    tokens_lc = [t.lower() for t in tokens]

    items = _collect_search_items(tokens, tokens_lc)
    items = _rank_and_truncate(items)
    items = await _maybe_llm_rerank_items(request, q, items)
    groups = _build_artist_groups(items)
    _strip_scores(items, groups)

    return web.json_response({"ok": True, "groups": groups, "items": items})
