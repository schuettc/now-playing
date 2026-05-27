"""Read-side aggregations: album stats, top albums, heatmap, recent plays."""
from __future__ import annotations

import sqlite3
import time

from .db import ALBUM_SESSION_GAP_S, MIN_TRACKS_PER_SESSION, _conn, log


def _bucket_album_sessions(rows) -> list[dict]:
    """Group ascending-ordered (started_at, ended_at, track_position) rows
    into session buckets. A new session starts when started_at exceeds the
    previous row's ended_at by more than ALBUM_SESSION_GAP_S.

    Returns a list of `{started_at, ended_at, tracks}` dicts where `tracks`
    is the set of distinct `track_position` values seen during the session
    (None is treated as one bucket — we don't know if multiple-NULL rows
    are the same track replayed or different tracks unidentified).
    """
    sessions: list[dict] = []
    for r in rows:
        started = int(r[0])
        ended = int(r[1])
        track = r[2] if len(r) > 2 else None
        if not sessions or (started - sessions[-1]["ended_at"]) > ALBUM_SESSION_GAP_S:
            sessions.append({
                "started_at": started,
                "ended_at": ended,
                "tracks": {track},
            })
        else:
            sessions[-1]["ended_at"] = max(sessions[-1]["ended_at"], ended)
            sessions[-1]["tracks"].add(track)
    return sessions


def _qualifying_sessions(
    rows, *, now: int, min_tracks: int = MIN_TRACKS_PER_SESSION,
) -> list[dict]:
    """Filter session buckets to those that should count toward play
    statistics:

    - Drop the in-progress session — the most recent bucket whose
      ended_at is within ALBUM_SESSION_GAP_S of `now`. The user is
      probably listening right now and we don't want the kiosk to claim
      they've "played N+1 times" while the play is still happening.
    - Drop any session with fewer than `min_tracks` distinct
      `track_position` values. Filters out playlist drive-bys where one
      track of the album happened to play.
    """
    sessions = _bucket_album_sessions(rows)
    if sessions and (now - sessions[-1]["ended_at"]) <= ALBUM_SESSION_GAP_S:
        sessions = sessions[:-1]
    return [s for s in sessions if len(s["tracks"]) >= min_tracks]


def _count_album_sessions(rows, *, now: int) -> int:
    """Count qualifying album listening sessions for play_count.

    `rows` is an ascending-ordered iterable of (started_at, ended_at,
    track_position) — sqlite3.Row indexes positionally and by name.
    Pass `now` (unix seconds) so the in-progress-session check is
    testable.
    """
    return len(_qualifying_sessions(rows, now=now))


def _safe_query(
    sql: str, params: tuple = (), *, label: str, default,
):
    """Run a SELECT and return its rows, or `default` on sqlite3.Error.
    `label` is used in the warning log line."""
    try:
        with _conn() as conn:
            return conn.execute(sql, params).fetchall()  # skylos: ignore SKY-D211 — sql comes only from module-level constants (_TOP_ALBUMS_SQL, _HEATMAP_SQL, _ALBUM_ROWS_SQL); user input flows through params placeholders
    except sqlite3.Error as e:
        log.warning("%s failed: %r", label, e)
        return default


def _fetch_album_rows(release_id: int) -> list[sqlite3.Row] | None:
    """Pull all (started_at, ended_at, track_position) rows for one release
    ordered ascending. Returns None on sqlite error (already logged).
    `track_position` is included so session-bucketing can count distinct
    tracks per session for the MIN_TRACKS_PER_SESSION filter."""
    return _safe_query(
        """
        SELECT started_at, ended_at, track_position
        FROM plays
        WHERE release_id = ?
        ORDER BY started_at ASC
        """,
        (release_id,),
        label="get_album_stats",
        default=None,
    )


def get_album_stats(release_id: int, *, now: int | None = None) -> dict | None:
    """Aggregate play stats for one release.

    A session counts toward `play_count` only when it includes at least
    MIN_TRACKS_PER_SESSION distinct tracks AND it isn't the currently-in-
    progress session. This filters two failure modes:

    1. Streaming playlists that include a single track of the album would
       otherwise inflate that album's "Played N times" stat with plays
       the user never actually directed at the album.
    2. The kiosk should not claim "Played N+1 times" *while* the user is
       in the middle of session N+1.

    `last_played_at` and `first_played_at` reflect *qualifying* sessions
    only, so the displayed "Last X ago" matches the count. Returns None
    when no rows exist at all (lets the kiosk hide the stats panel). If
    rows exist but none qualify, returns play_count=0 with both
    timestamps null.
    """
    rows = _fetch_album_rows(release_id)
    if not rows:
        return None
    if now is None:
        now = int(time.time())
    qualifying = _qualifying_sessions(rows, now=now)
    last_played_at = qualifying[-1]["ended_at"] if qualifying else None
    first_played_at = qualifying[0]["started_at"] if qualifying else None
    return {
        "release_id": release_id,
        "play_count": len(qualifying),
        "last_played_at": last_played_at,
        "first_played_at": first_played_at,
    }


def _count_in_window_sessions(
    pairs: list[tuple[int, int]], since_ts: int,
) -> tuple[int, int]:
    """Walk ascending (started, ended) pairs for a single group; return
    (session_count, latest_started) counting only sessions whose first row
    started within the window."""
    sessions = 0
    last_in_window = 0
    prev_ended: int | None = None
    for started, ended in pairs:
        is_session_start = (
            prev_ended is None or (started - prev_ended) > ALBUM_SESSION_GAP_S
        )
        if is_session_start and started >= since_ts:
            sessions += 1
            if started > last_in_window:
                last_in_window = started
        prev_ended = ended
    return sessions, last_in_window


# Full table pull for top_albums(). We can't filter by since_ts in SQL alone,
# because a session that started before since_ts but extended into the window
# needs its earlier rows to correctly identify session boundaries — but per
# the spec a session "counts" only if its first row falls in the window, so
# we walk the full history per release and filter at the end. Cheap: a single
# release rarely has thousands of rows.
#
# match_method and title are included so the Python layer can filter corrupted
# Shazam-only rows where the track title leaked into the album field
# (release_id=NULL, match_method='shazam', lower(album)==lower(title)).
_TOP_ALBUMS_SQL = """
    SELECT release_id, album, artist, match_method, title, started_at, ended_at
    FROM plays
    WHERE album IS NOT NULL AND album != ''
    ORDER BY release_id, album, artist, started_at ASC
"""


def _is_corrupted_shazam_row(r: sqlite3.Row) -> bool:
    """Return True for rows where the track title leaked into the album field.

    Targets only Shazam-only rows with no confirmed Discogs release, where
    album and title are the same string (case-insensitive). This avoids
    dropping legitimate title tracks (e.g. "Black Sabbath" on the "Black
    Sabbath" release with a non-NULL release_id).
    """
    if r["release_id"] is not None:
        return False
    if (r["match_method"] or "") != "shazam":
        return False
    album = (r["album"] or "").strip().lower()
    title = (r["title"] or "").strip().lower()
    return bool(album and title and album == title)


def _merge_null_release_groups(
    groups: dict[tuple, list],
) -> dict[tuple, list]:
    """Merge NULL-release_id pair-lists into their corresponding non-NULL groups.

    When plays for the same (album, artist) have some rows with release_id=NULL
    (Shazam-only matches) and others with a real release_id (Discogs-confirmed),
    the NULL rows form a separate group that produces an art-less card. This
    function folds the NULL pairs into the best non-NULL group so the album
    appears once and its art URL is valid.

    Tie-breaking when multiple non-NULL release_ids exist for the same
    (album, artist): merge into the group with the most raw play rows (pairs);
    on a tie pick the lowest (numerically smallest / oldest) release_id.
    Raw pair count is used rather than session count to avoid a double-pass
    through _count_in_window_sessions().

    After merging, the combined pair list is re-sorted by started_at so
    _count_in_window_sessions() (which assumes ascending order) produces
    correct session boundaries.
    """
    # Index non-NULL groups by (album, artist) → list of (release_id, pairs)
    non_null: dict[tuple, list[tuple[int, list]]] = {}
    for (rid, album, artist), pairs in groups.items():
        if rid is not None:
            key = (album, artist)
            non_null.setdefault(key, []).append((rid, pairs))

    # For each NULL group that has a matching (album, artist) in non_null,
    # pick the best target and fold in the pairs.
    merged: dict[tuple, list] = {}
    for (rid, album, artist), pairs in groups.items():
        if rid is not None:
            merged[(rid, album, artist)] = pairs
            continue
        # NULL group — find best non-NULL target for this (album, artist)
        candidates = non_null.get((album, artist))
        if not candidates:
            # No confirmed release — keep NULL group as-is
            merged[(None, album, artist)] = pairs
            continue
        # Pick the group with the most pairs; break ties with the lowest rid.
        # The rid from the picked tuple is not bound — the merge happens via
        # the list-reference reassignment below (best_pairs.extend updates the
        # group's pairs in place; see comment after the merge).
        _, best_pairs = max(
            candidates,
            key=lambda t: (len(t[1]), -t[0]),
        )
        # Merge and re-sort so session boundary detection stays correct
        best_pairs.extend(pairs)
        best_pairs.sort(key=lambda p: p[0])
        # The merged pairs are already in place via the list reference;
        # no need to reassign — groups[(best_rid, album, artist)] == best_pairs

    return merged


def top_albums(since_ts: int, limit: int) -> list[dict]:
    """Group plays by (release_id, album, artist) and return the top N albums
    by *session count* since `since_ts`. An album session = a contiguous run
    of rows for the same release with no gap > ALBUM_SESSION_GAP_S. A session
    counts toward the window if its first row's started_at >= since_ts.
    Rows without an album are excluded — no useful card to render for them.

    Two data-quality fixes applied before grouping:
    1. Corrupted Shazam rows (release_id=NULL, match_method='shazam',
       album==title) are filtered out to remove cases where the track title
       leaked into the album field.
    2. NULL release_id groups for the same (album, artist) as a confirmed
       Discogs group are merged into the confirmed group so a single card
       with a valid art URL is emitted instead of two separate cards.
    """
    rows = _safe_query(_TOP_ALBUMS_SQL, label="top_albums()", default=[])

    # Filter corrupted Shazam rows before grouping
    rows = [r for r in rows if not _is_corrupted_shazam_row(r)]

    # Group by (release_id, album, artist)
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r["release_id"], r["album"], r["artist"])
        groups.setdefault(key, []).append((int(r["started_at"]), int(r["ended_at"])))

    # Merge NULL-release_id groups into their non-NULL counterparts
    groups = _merge_null_release_groups(groups)

    out: list[dict] = []
    for (release_id, album, artist), pairs in groups.items():
        sessions, last_in_window = _count_in_window_sessions(pairs, since_ts)
        if sessions > 0:
            out.append(
                {
                    "release_id": release_id,
                    "album": album,
                    "artist": artist,
                    "plays": sessions,
                    "last_played": last_in_window,
                }
            )

    out.sort(key=lambda d: (-d["plays"], -d["last_played"]))
    return out[:limit]


# Full table pull for heatmap() bucketing. date() in SQLite gives us the
# local-time day string for the session-start timestamp.
_HEATMAP_SQL = """
    SELECT release_id, started_at, ended_at,
           date(started_at, 'unixepoch', 'localtime') AS day
    FROM plays
    ORDER BY release_id, started_at ASC
"""


def heatmap(since_ts: int) -> list[dict]:
    """Per-day album-session counts (local time) since `since_ts`. One row =
    one album listening session that *started* that day. Returns list of
    {date: 'YYYY-MM-DD', count: int} ordered ascending by date.

    We walk every row ordered by (release_id, started_at), flag session-starts
    in Python, and only bucket those whose started_at >= since_ts.
    """
    rows = _safe_query(_HEATMAP_SQL, label="heatmap()", default=[])

    counts: dict[str, int] = {}
    prev_release: object = object()  # sentinel; never equals a real release_id
    prev_ended: int | None = None
    for r in rows:
        release = r["release_id"]
        started = int(r["started_at"])
        ended = int(r["ended_at"])
        is_session_start = (
            release != prev_release
            or prev_ended is None
            or (started - prev_ended) > ALBUM_SESSION_GAP_S
        )
        if is_session_start and started >= since_ts:
            counts[r["day"]] = counts.get(r["day"], 0) + 1
        prev_release = release
        prev_ended = ended

    return [{"date": d, "count": counts[d]} for d in sorted(counts.keys())]


def recent(limit: int = 50, since: int | None = None) -> list[dict]:
    """Return the most recent plays, newest first.

    `since` is a unix-seconds floor; rows with started_at < since are excluded.
    """
    try:
        with _conn() as conn:
            if since is not None:
                rows = conn.execute(
                    "SELECT * FROM plays WHERE started_at >= ? ORDER BY started_at DESC LIMIT ?",
                    (since, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM plays ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error as e:
        log.warning("recent() failed: %r", e)
        return []
