"""SQLite schema + write path for play history.

Schema:
    plays(
        id INTEGER PRIMARY KEY,
        release_id INTEGER,        -- nullable for shazam-only matches
        track_position TEXT,
        artist TEXT,
        title TEXT,
        album TEXT,
        match_method TEXT,         -- shazam / predicted / sonos-didl / sonos-polled / user-identified / user-selected / unmatched
        source TEXT,               -- vinyl / streaming / airplay / ...
        started_at INTEGER,        -- unix seconds
        ended_at INTEGER           -- unix seconds (extended on repeat heartbeats)
    )

The orchestrator calls `record_play(payload)` for every published recognition
that has a title. Consecutive heartbeats for the same (release_id, track_position,
artist, title) within COALESCE_WINDOW_S extend the existing row's ended_at
instead of creating a new one — so a 4-minute track shows up as ONE row,
not 16 rows from the heartbeat cadence.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("nowplaying.history")

REPO_ROOT = Path(__file__).resolve().parents[3]
PI_DIR = REPO_ROOT / "pi"
DATA_DIR = PI_DIR / "data"
DB_PATH = DATA_DIR / "play_history.sqlite"

COALESCE_WINDOW_S = 60  # extend an existing row if heartbeats arrive within this window
ALBUM_SESSION_GAP_S = 30 * 60  # gap > this between consecutive same-release rows starts a new session

# A session only counts toward an album's play count if at least this many
# distinct tracks from the album were heard during it. Prevents streaming
# playlists that happen to include a single track from an album from
# inflating that album's "Played N times" stat. None track_positions are
# treated as a single bucket — conservative for the unknown-track case.
MIN_TRACKS_PER_SESSION = 3

# Serializes concurrent record_play calls — without this, a Sonos event and a
# Vinyl heartbeat arriving simultaneously could both SELECT the same "last" row
# and both INSERT, creating duplicate rows for the same play.
_write_lock = asyncio.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the schema if missing. Called once at startup; subsequent
    `_conn()` calls assume the directory and file exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        # WAL mode: orchestrator writes (~every 15s) and /history reads
        # concurrently — without WAL, readers can collide with writers and
        # raise "database is locked" on the Pi's SD card under load.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plays (
                id INTEGER PRIMARY KEY,
                release_id INTEGER,
                track_position TEXT,
                artist TEXT,
                title TEXT,
                album TEXT,
                match_method TEXT,
                source TEXT,
                started_at INTEGER NOT NULL,
                ended_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plays_started_at ON plays(started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plays_release_id ON plays(release_id) WHERE release_id IS NOT NULL"
        )
        conn.commit()


def _compat(a: str | None, b: str | None) -> bool:
    """Loose-equality used for coalesce — treats None on either side as a
    wildcard so the polled-enrichment case (Sonos publishes a partial payload
    first, then re-publishes the same track with enriched metadata) updates
    the existing row instead of creating a duplicate.
    """
    if a is None or b is None:
        return True
    return (a or "") == (b or "")


def _row_matches(row: sqlite3.Row, payload: dict) -> bool:
    """Coalesce-target check. Title is the strong key. release_id, artist,
    album, and track_position are wildcard-compared so None→value enrichment
    doesn't split one play into two rows. Source MUST match — playing the
    same track via vinyl and Sonos within the coalesce window should be
    two separate plays, not one.
    """
    if (row["source"] or "") != (payload.get("source") or ""):
        return False
    return (
        _compat(row["release_id"], payload.get("release_id"))
        and _compat(row["track_position"], payload.get("track_position"))
        and _compat(row["title"], payload.get("title"))
        and _compat(row["artist"], payload.get("artist"))
        and _compat(row["album"], payload.get("album"))
    )


# Confirmed match_method values overwrite predicted/unmatched on coalesce.
# A predicted entry that Shazam later confirms should be audited as a Shazam
# play, not a prediction. See docs/features/tracklist-aware-advancement/.
_CONFIRMED_METHODS = (
    "shazam",
    "sonos-didl",
    "sonos-polled",
    "user-selected",
    "user-identified",
)


def _within_coalesce_window(
    last: sqlite3.Row | None, now: int, extend_only: bool = False,
) -> bool:
    """Clock-jump guard: if the system clock has moved backward (Pi has no
    RTC; NTP sync after long offline can jump), `now` may be less than the
    previous row's ended_at. Don't coalesce in that case — the resulting row
    would have ended_at < started_at.

    ``extend_only`` drops the COALESCE_WINDOW_S upper bound (keeping the
    clock-jump guard): the broadcaster has already ruled this publish
    content-identical to what's on screen, so it is the same continuous
    play no matter how long ago the last heartbeat was. The window only
    existed to separate distinct plays; the broadcaster now does that job.
    """
    if last is None:
        return False
    last_end = int(last["ended_at"])
    if now < last_end:
        return False
    return extend_only or (now - last_end) <= COALESCE_WINDOW_S


def _apply_coalesce_update(
    conn: sqlite3.Connection, row_id: int, payload: dict, now: int,
) -> None:
    """Run the UPDATE that extends ended_at and upgrades None fields with
    newly-arrived values. match_method: confirmed methods (shazam/sonos-*/
    user-*) overwrite predicted/unmatched; otherwise existing value is kept."""
    new_method = payload.get("match_method")
    method_overwrite = new_method if new_method in _CONFIRMED_METHODS else None
    conn.execute(
        """
        UPDATE plays SET
            ended_at = ?,
            release_id     = COALESCE(release_id, ?),
            artist         = COALESCE(artist, ?),
            title          = COALESCE(title, ?),
            album          = COALESCE(album, ?),
            track_position = COALESCE(track_position, ?),
            match_method   = CASE
                WHEN ? IS NOT NULL THEN ?
                ELSE COALESCE(match_method, ?)
            END
        WHERE id=?
        """,
        (
            now,
            payload.get("release_id"),
            payload.get("artist"),
            payload.get("title"),
            payload.get("album"),
            payload.get("track_position"),
            method_overwrite,
            method_overwrite,
            new_method,
            row_id,
        ),
    )
    conn.commit()


def _coalesce_existing_row(
    conn: sqlite3.Connection, last: sqlite3.Row, payload: dict, now: int,
) -> dict:
    """Extend an existing row's ended_at and upgrade any None fields with
    newly-arrived values (polled enrichment). Existing non-None values are
    preserved. Returns the row-info dict for the updated row.
    """
    row_id = last["id"]
    _apply_coalesce_update(conn, row_id, payload, now)
    row = conn.execute(
        "SELECT id, started_at, ended_at, artist, title, album FROM plays WHERE id = ?",
        (row_id,),
    ).fetchone()
    return {
        "id": int(row["id"]),
        "started_at": int(row["started_at"]),
        "ended_at": int(row["ended_at"]),
        "artist": row["artist"],
        "title": row["title"],
        "album": row["album"],
        "inserted": False,
    }


def _insert_new_row(
    conn: sqlite3.Connection, payload: dict, title: str, now: int,
) -> dict:
    """Insert a fresh play row and return its row-info dict."""
    cur = conn.execute(
        """
        INSERT INTO plays
            (release_id, track_position, artist, title, album,
             match_method, source, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.get("release_id"),
            payload.get("track_position"),
            payload.get("artist"),
            title,
            payload.get("album"),
            payload.get("match_method"),
            payload.get("source"),
            now,
            now,
        ),
    )
    conn.commit()
    return {
        "id": int(cur.lastrowid),
        "started_at": now,
        "ended_at": now,
        "artist": payload.get("artist"),
        "title": title,
        "album": payload.get("album"),
        "inserted": True,
    }


def _write_play_row(
    payload: dict, title: str, now: int, extend_only: bool = False,
) -> dict | None:
    """Open a connection, decide coalesce-vs-insert, and write the row.
    Returns the row-info dict, or None when ``extend_only`` had nothing to
    extend. Raises sqlite3.Error on DB failure — caller handles logging.

    ``extend_only`` marks a publish the broadcaster suppressed as
    content-identical: extend the matching row past the coalesce window,
    but NEVER insert — a suppressed publish is by definition not a new
    play, so if it doesn't match the last row (stale-cache re-emit of an
    already-ended track) it must write nothing.
    """
    with _conn() as conn:
        last = conn.execute(
            "SELECT * FROM plays ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if (
            last is not None
            and _row_matches(last, payload)
            and _within_coalesce_window(last, now, extend_only)
        ):
            return _coalesce_existing_row(conn, last, payload, now)
        if extend_only:
            return None
        return _insert_new_row(conn, payload, title, now)


def _record_play_sync(payload: dict, extend_only: bool = False) -> dict | None:
    """Synchronous body of record_play; intended to run via asyncio.to_thread.

    No-op when the payload has no title (idle / unmatched / pre-recognition)
    or when state is not actively playing.

    Returns a dict describing the row that was written
    (`{id, started_at, ended_at, artist, title, album, inserted}`) so the
    async wrapper can decide whether to fire a Last.fm scrobble. Returns
    None when no row was written (idle / not playing / DB error).
    """
    title = payload.get("title")
    if not title:
        return None
    if payload.get("state") != "PLAYING":
        return None
    now = int(time.time())
    try:
        return _write_play_row(payload, title, now, extend_only)
    except sqlite3.Error as e:
        log.warning("record_play failed: %r", e)
        return None


async def record_play(payload: dict, extend_only: bool = False) -> None:
    """Async wrapper — runs the SQLite write in a thread so the asyncio
    event loop is never blocked by disk I/O. Serialized via _write_lock
    so concurrent callers can't race the SELECT-then-INSERT/UPDATE.

    ``extend_only`` (set when the broadcaster suppressed the publish as
    content-identical) extends the current row without inserting; see
    _write_play_row. On a successful write, fires a fire-and-forget
    Last.fm scrobble task (no-op when env vars are unset)."""
    # Local import: scrobble module imports from db for log/constants in the
    # future, and this keeps the import graph one-directional at module load.
    from .scrobble import _safe_scrobble

    async with _write_lock:
        row_info = await asyncio.to_thread(_record_play_sync, payload, extend_only)
    if row_info is not None:
        duration = payload.get("duration_seconds")
        # Fire-and-forget; never block the caller.
        try:
            asyncio.create_task(_safe_scrobble(row_info, duration))
        except RuntimeError as e:
            # No running loop (e.g. unit test invoked record_play directly
            # without a loop) — skip scrobble dispatch.
            log.debug("scrobble task dispatch skipped (no running loop): %r", e)
