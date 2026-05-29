"""Local Discogs catalog access — read-only queries against pi/data/discogs.sqlite."""
from __future__ import annotations

import difflib
import functools
import logging
import sqlite3
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "pi" / "data" / "discogs.sqlite"


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)  # strip parentheticals
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def open_ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@functools.lru_cache(maxsize=512)
def rid_to_album(release_id: int) -> Optional[tuple[str, str]]:
    """Return ``(artist, title)`` for a release_id, or None if not cached
    locally. LRU-cached to keep the synchronous SQLite path off the
    aiohttp event loop on hot ``/art/<id>`` requests — the first miss
    opens a connection, every subsequent hit is a dict lookup.

    Release metadata is immutable within our snapshot, so no invalidation
    is needed. Callers wanting to refresh after a sync should reach into
    ``rid_to_album.cache_clear()``.
    """
    try:
        with open_ro() as con:
            row = con.execute(
                "SELECT artist, title FROM releases WHERE id = ?", (release_id,),
            ).fetchone()
    except sqlite3.OperationalError as e:
        logger.debug("rid_to_album: SQL failed for release_id=%s: %r", release_id, e)
        return None
    if not row:
        return None
    artist = (row["artist"] or "").strip()
    title = (row["title"] or "").strip()
    if not artist or not title:
        return None
    return artist, title


@functools.lru_cache(maxsize=512)
def first_position_per_side(release_id: int) -> dict[str, str]:
    """Return ``{side: first-track-position}`` for the given release.

    "First" means *first in DB insertion order* (rowid), which matches
    physical play order because :func:`discogs_sync.fetch_detail` deletes
    a release's tracks then re-inserts them in the order returned by the
    Discogs API. So the first row with ``side="D"`` for release X is
    physically the first track of side D, regardless of whether its
    ``position`` is "D1" (per-side numbering) or "D15" (cumulative).

    This replaces the old regex-based ``_is_side_first_track`` check,
    which only matched literal "[A-D]1" — unfair to cumulative-numbered
    multi-LPs whose first side-D track is "D15".

    Returns ``{}`` if the catalog is unavailable. Cache is keyed on
    release_id; orchestrator restart on Discogs sync handles
    invalidation in practice (sync runs as a separate script).
    """
    try:
        with open_ro() as con:
            rows = con.execute(
                "SELECT side, position FROM tracks "
                "WHERE release_id = ? AND side IS NOT NULL "
                "ORDER BY rowid",
                (release_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    firsts: dict[str, str] = {}
    for row in rows:
        side = row["side"]
        position = row["position"]
        if not side or not position:
            continue
        if side not in firsts:
            firsts[side] = position
    return firsts


@functools.lru_cache(maxsize=256)
def _ambiguous_titles_for_artist(artist_lower: str, title_lower: str) -> bool:
    """True iff ≥2 releases in the Discogs catalog share this
    (case-insensitive) artist+title. Cached because we hit this on every
    Shazam→Discogs recognition and the answer is immutable until a sync.
    Callers wanting to refresh after a sync should call
    ``_ambiguous_titles_for_artist.cache_clear()``.
    """
    if not artist_lower or not title_lower:
        return False
    try:
        with open_ro() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM releases "
                "WHERE LOWER(artist) = ? AND LOWER(title) = ?",
                (artist_lower, title_lower),
            ).fetchone()
    except sqlite3.OperationalError as e:
        logger.debug(
            "_ambiguous_titles_for_artist: SQL failed for artist=%r title=%r: %r",
            artist_lower, title_lower, e,
        )
        return False
    return bool(row and int(row["n"]) >= 2)


def _disambiguate_album_title(
    con: sqlite3.Connection,
    release_id: int,
    artist: str,
    title: str,
    year: Optional[int],
    catno: Optional[str],
) -> Optional[str]:
    """Compute the disambiguation suffix for a release when its artist
    has ≥2 releases sharing this title. Returns the disambiguated string
    or None when no disambiguation is needed (or possible).

    - Year suffix when year is non-null and at least one same-title
      sibling has a *different* year.
    - Catno suffix when all same-title siblings share the year (boxset
      reissue) — year alone wouldn't disambiguate.
    - None when year is null AND no catno (no useful suffix available).
    """
    artist_lower = (artist or "").strip().lower()
    title_lower = (title or "").strip().lower()
    if not artist_lower or not title_lower:
        return None
    if not _ambiguous_titles_for_artist(artist_lower, title_lower):
        return None
    if year is not None:
        try:
            sibling_years = {
                r["year"]
                for r in con.execute(
                    "SELECT year FROM releases "
                    "WHERE LOWER(artist) = ? AND LOWER(title) = ? AND id != ?",
                    (artist_lower, title_lower, release_id),
                )
            }
        except sqlite3.OperationalError:
            return None
        if any(y != year for y in sibling_years if y is not None):
            return f"{title} ({year})"
        # All siblings share this year (or have null year). Fall through
        # to catno tiebreaker.
    if catno:
        return f"{title} ({catno})"
    return None


def get_release(release_id: int) -> Optional[dict]:
    """Return release row + playable leaf tracks. Suite parents
    (``is_suite_parent=1``) are excluded so state, kiosk tracklists,
    cumulative-duration math, and end-of-side gates only see the
    movements the needle can actually land on. Reverse-lookup queries
    that need to match Shazam's parent-suite title use a separate path
    that reads ``tracks`` directly without this filter.

    When ≥2 releases by the same artist share this title (eponymous
    eg. American Football LP1/LP2/LP3), the returned dict also has a
    ``disambiguated_album`` key — ``"{title} ({year})"`` or
    ``"{title} ({catno})"`` if year alone doesn't disambiguate.
    Callers that need the bare canonical title (search) read ``title``;
    callers that render to the user (kiosk, scrobble) prefer
    ``disambiguated_album`` over ``title``.
    """
    try:
        with open_ro() as con:
            row = con.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["tracks"] = [
                dict(t)
                for t in con.execute(
                    "SELECT position, side, title, duration_seconds, clean_title "
                    "FROM tracks WHERE release_id = ? AND is_suite_parent = 0 "
                    "ORDER BY position",
                    (release_id,),
                )
            ]
            disambiguated = _disambiguate_album_title(
                con,
                release_id,
                out.get("artist") or "",
                out.get("title") or "",
                out.get("year"),
                out.get("catno"),
            )
            if disambiguated:
                out["disambiguated_album"] = disambiguated
            return out
    except sqlite3.OperationalError as e:
        logger.debug("get_release: SQL failed for release_id=%s: %r", release_id, e)
        return None


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _string_sim(a: str, b: str) -> float:
    """Character-level similarity ratio. Tolerant of spelling variants
    (Saturday Saviour ≈ Saturday Savior) where token Jaccard would drop."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _is_side_first_track(release_id: int, position: str | None) -> bool:
    """True if the position is physically the first track on its side.

    Looks up ``first_position_per_side(release_id)`` and compares the
    given position against that side's recorded first-position string.
    This correctly handles both per-side numbering (D1 wins on its
    side) and cumulative numbering (D15 also wins on its side, when
    D15 is the first row inserted with side="D").
    """
    if not position:
        return False
    p = position.strip().upper()
    if not p:
        return False
    side = p[0]
    if not side.isalpha():
        return False
    firsts = first_position_per_side(release_id)
    expected = firsts.get(side)
    if not expected:
        return False
    return expected.strip().upper() == p


def _pick_split_winner(
    left_hit: Optional[dict], left: str,
    right_hit: Optional[dict], right: str,
) -> tuple[Optional[dict], str]:
    """Choose the better half-hit; stable tie-breaker favors the left."""
    if left_hit is None and right_hit is None:
        return None, ""
    if left_hit is None:
        return right_hit, right
    if right_hit is None:
        return left_hit, left
    if left_hit.get("match_score", 0) >= right_hit.get("match_score", 0):
        return left_hit, left
    return right_hit, right


_MULTIPART_POSITION_RE = re.compile(r"^[A-Z]\d+\.\s*[IVX]+$")


def _has_multipart_positions(tracks: list[dict]) -> bool:
    """True when at least one position looks like a sub-movement of a
    multi-part suite (e.g. ``D1. I``, ``A2. III``). Used as a signal
    that a Shazam parent-suite name (e.g. "Homecoming") may legitimately
    fail to match any leaf title row."""
    for t in tracks or []:
        if _MULTIPART_POSITION_RE.match((t.get("position") or "").strip()):
            return True
    return False


def _suite_fallback(
    artist: str, preferred_release_id: int,
) -> Optional[dict]:
    """Last-resort match for multi-part suites. Returns the sticky
    release with no matched_track_position so the caller's positional
    guess is preserved. Declines for plain LPs and for artist mismatch
    so this can't accidentally lock onto a stale sticky after a real
    album change.
    """
    rel = get_release(preferred_release_id)
    if rel is None:
        return None
    if not _has_multipart_positions(rel.get("tracks") or []):
        return None
    norm_target_artist = _normalize(artist)
    norm_rel_artist = _normalize(rel.get("artist") or "")
    if not norm_target_artist or not norm_rel_artist:
        return None
    if _string_sim(norm_target_artist, norm_rel_artist) < 0.7:
        return None
    rel["match_score"] = 0
    rel["matched_track_position"] = None
    rel["matched_track_title"] = None
    rel["suite_fallback"] = True
    logger.info(
        "discogs reverse-lookup: suite-name fallback — preferred release_id=%d retained",
        preferred_release_id,
    )
    return rel


def _suite_artist_scan(artist: str) -> Optional[dict]:
    """Cold-start suite fallback. When no sticky release is available
    (e.g. user dropped directly on D2 "Homecoming" after orchestrator
    restart) scan the collection for releases by this artist whose
    tracklist has multi-part positions. Returns the release iff there
    is exactly one such match — multiple matches are ambiguous and we
    decline rather than guess.
    """
    norm_target_artist = _normalize(artist)
    if not norm_target_artist:
        return None
    first_token = norm_target_artist.split(" ")[0]
    try:
        with open_ro() as con:
            rows = con.execute(
                """
                SELECT DISTINCT releases.id AS release_id, releases.artist
                FROM tracks JOIN releases ON tracks.release_id = releases.id
                WHERE LOWER(releases.artist) LIKE ?
                """,
                (f"%{first_token}%",),
            ).fetchall()
    except sqlite3.OperationalError as e:
        logger.debug("_suite_artist_scan: SQL failed for artist=%r: %r", artist, e)
        return None
    candidates: list[dict] = []
    for row in rows:
        rel_artist = row["artist"] or ""
        norm_rel_artist = _normalize(rel_artist)
        if not norm_rel_artist:
            continue
        if _string_sim(norm_target_artist, norm_rel_artist) < 0.7:
            continue
        rel = get_release(row["release_id"])
        if rel is None:
            continue
        if not _has_multipart_positions(rel.get("tracks") or []):
            continue
        candidates.append(rel)
    if len(candidates) != 1:
        if len(candidates) > 1:
            logger.info(
                "discogs reverse-lookup: suite-name artist-scan ambiguous "
                "for artist=%r (%d candidates) — declining",
                artist, len(candidates),
            )
        return None
    rel = candidates[0]
    rel["match_score"] = 0
    rel["matched_track_position"] = None
    rel["matched_track_title"] = None
    rel["suite_fallback"] = True
    logger.info(
        "discogs reverse-lookup: suite-name artist-scan — "
        "artist=%r → release_id=%d",
        artist, rel.get("id", 0),
    )
    return rel


def find_by_artist_title(
    artist: str,
    title: str,
    *,
    preferred_release_id: int | None = None,
) -> Optional[dict]:
    """Reverse-lookup with slash-split + suite-name fallbacks for Shazam.

    Runs the primary lookup first. On miss:

    1. Slash-split fallback — Shazam returns medley titles like
       ``"Changeling / Transmission"``; split on ` / ` and pick the
       higher-scoring half. Recurses naturally for 3+ track medleys.
    2. Suite-name fallback — multi-part suites (American Idiot's
       "Homecoming" → D1.I–V) appear in Discogs as leaf movements only;
       Shazam returns the parent suite name. Two flavors:
         a. Sticky path — when a ``preferred_release_id`` is in flight
            and it's a multi-part release with matching artist, keep
            it so the kiosk preserves album art / tracklist context.
         b. Artist-scan path — cold start (no sticky). Scan the
            collection for releases by this artist with multi-part
            positions; fire only when exactly one match exists.

    Slash detection is on the raw `title` argument, NOT a normalized
    form — `_normalize` strips `/` to whitespace, so a normalized
    check would never fire.
    """
    primary = _find_by_artist_title_primary(
        artist, title, preferred_release_id=preferred_release_id,
    )
    if primary is not None:
        return primary
    if title and " / " in title:
        left, right = title.split(" / ", 1)
        left = left.strip()
        right = right.strip()
        left_hit = (
            _find_by_artist_title_primary(
                artist, left, preferred_release_id=preferred_release_id,
            )
            if left
            else None
        )
        right_hit = (
            find_by_artist_title(
                artist, right, preferred_release_id=preferred_release_id,
            )
            if right
            else None
        )
        winner, winning_half = _pick_split_winner(left_hit, left, right_hit, right)
        if winner is not None:
            logger.debug(
                "discogs reverse-lookup: combined title %r resolved via split -> %r (release_id=%d)",
                title, winning_half, winner.get("id", 0),
            )
            return winner
    if preferred_release_id is not None:
        sticky = _suite_fallback(artist, preferred_release_id)
        if sticky is not None:
            return sticky
    return _suite_artist_scan(artist)


def _score_candidate(
    row: dict,
    target_artist_tokens: set[str],
    target_title_tokens: set[str],
    norm_artist: str,
    norm_track_title: str,
    preferred_release_id: int | None,
) -> Optional[dict]:
    """Score one (track, release) row. Returns a candidate dict or None
    if the row doesn't clear the artist/title similarity floors."""
    norm_track = _normalize(row["track_title"] or "")
    norm_db_artist = _normalize(row["artist"])
    a_tokens = set(norm_db_artist.split())
    tr_tokens = set(norm_track.split())
    if not a_tokens or not tr_tokens:
        return None
    artist_sim = max(
        _jaccard(target_artist_tokens, a_tokens),
        _string_sim(norm_artist, norm_db_artist),
    )
    track_sim = max(
        _jaccard(target_title_tokens, tr_tokens),
        _string_sim(norm_track_title, norm_track),
    )
    if artist_sim < 0.5 or track_sim < 0.7:
        return None
    base = int(100 * (0.3 * artist_sim + 0.65 * track_sim))
    biases = 0
    side_first = _is_side_first_track(row["release_id"], row["position"])
    if side_first:
        biases += 15
    return {
        "row": dict(row),
        "base": base,
        "biases": biases,
        "side_first": side_first,
        "is_sticky": (
            preferred_release_id is not None
            and row["release_id"] == preferred_release_id
        ),
    }


def _rank_candidates(
    candidates: list[dict],
    sticky_bonus: int = 25,
) -> list[tuple[int, int, int, dict]]:
    """Score each candidate WITH sticky bonus and rank descending by
    (score_with_sticky, year). Drops anything below the 60 floor.
    Returns a list of (score_with_sticky, base_score, year, row) tuples."""
    scored: list[tuple[int, int, int, dict]] = []
    for c in candidates:
        base_with_biases = c["base"] + c["biases"]
        score_with_sticky = base_with_biases + (sticky_bonus if c["is_sticky"] else 0)
        if score_with_sticky < 60:
            continue
        year = c["row"].get("year") or 0
        scored.append((score_with_sticky, base_with_biases, year, c["row"]))
    scored.sort(key=lambda t: (t[0], t[2]), reverse=True)
    return scored


def _collect_alternates(
    scored: list[tuple[int, int, int, dict]],
    winner_base: int,
    winner_release_id: int,
    delta: int = 20,
    limit: int = 5,
) -> list[dict]:
    """Distinct releases within `delta` of the winner's base score
    (sticky-bonus-free, so the sticky lock doesn't suppress alternates)."""
    seen_ids = {winner_release_id}
    alternates: list[dict] = []
    for entry in sorted(scored, key=lambda t: (t[1], t[2]), reverse=True):
        base, row = entry[1], entry[3]
        rid = row["release_id"]
        if rid in seen_ids:
            continue
        if winner_base - base > delta:
            break
        seen_ids.add(rid)
        alternates.append({
            "release_id": rid,
            "album": row.get("album"),
            "year": row.get("year"),
            "format": row.get("format"),
            "track_position": row.get("position"),
            "track_title": row.get("track_title"),
            "score": base,
        })
        if len(alternates) >= limit:
            break
    return alternates


def _find_by_artist_title_primary(
    artist: str,
    title: str,
    *,
    preferred_release_id: int | None = None,
) -> Optional[dict]:
    """Primary reverse-lookup path: Shazam returns SONG title + ARTIST. We
    need the Discogs RELEASE containing that song.

    Strategy:
      1. Find tracks whose title matches (token-overlap) AND whose release's
         artist matches.
      2. Score by combined track-title and release-artist similarity, then
         apply biases:
            - First-on-side bonus: +15 (A1/B1/... — the dominant disambiguation
              signal since users almost always start records from the beginning).
            - Sticky-release bonus: +25 when the candidate matches the caller's
              preferred_release_id (the album currently in flight). Big enough
              to flip the choice when an equivalent-scoring alternate exists,
              not big enough to override a genuinely better artist/title match.
            - Vinyl-format bonus: +5 (slight LP preference).
            - Compilation penalty: -3 (small tiebreaker for the narrow case
              where the same song is A1 on both an LP and a compilation).
      3. Among remaining ties, prefer the most recent vinyl pressing (highest year).

    Returns the best release dict (with tracks) or None.
    """
    norm_artist = _normalize(artist)
    norm_track_title = _normalize(title)
    if not norm_artist or not norm_track_title:
        return None

    target_artist_tokens = set(norm_artist.split())
    target_title_tokens = set(norm_track_title.split())

    try:
        with open_ro() as con:
            # Pre-filter on artist (cheap LIKE) to avoid scanning all 4k tracks.
            first_artist_token = norm_artist.split(" ")[0]
            rows = con.execute(
                """
                SELECT releases.id AS release_id, releases.artist, releases.title AS album, releases.year, releases.format,
                       tracks.position, tracks.title AS track_title
                FROM tracks JOIN releases ON tracks.release_id = releases.id
                WHERE LOWER(releases.artist) LIKE ?
                """,
                (f"%{first_artist_token}%",),
            ).fetchall()
    except sqlite3.OperationalError as e:
        logger.debug(
            "_find_by_artist_title_primary: SQL failed for artist=%r title=%r: %r",
            artist, title, e,
        )
        return None

    # Two-pass scoring so the sticky bonus can be downgraded when a non-sticky
    # candidate has a stronger physical signal (side-first). Single-pass would let
    # sticky dominate even when the user is clearly on a different album.
    candidates = [
        cand for r in rows
        if (cand := _score_candidate(
            r, target_artist_tokens, target_title_tokens,
            norm_artist, norm_track_title, preferred_release_id,
        )) is not None
    ]
    if not candidates:
        return None
    # Score WITH sticky (to pick the winner). Alternates use the base score
    # (no sticky bonus) so the sticky lock doesn't shrink the alternate set.
    scored = _rank_candidates(candidates)
    if not scored:
        return None
    best = scored[0]
    full = get_release(best[3]["release_id"])
    if full is None:
        return None
    full["match_score"] = best[0]
    full["matched_track_position"] = best[3]["position"]
    full["matched_track_title"] = best[3]["track_title"]
    alternates = _collect_alternates(scored, best[1], best[3]["release_id"])
    if alternates:
        full["alternate_releases"] = alternates
    return full
