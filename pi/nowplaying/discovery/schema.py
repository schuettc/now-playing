"""``discovered.sqlite`` schema + connection helpers.

Parallel to ``pi/data/discogs.sqlite`` but keyed on MusicBrainz IDs
instead of integer Discogs release_ids. The catalog dispatcher in
``nowplaying.catalog`` reads from this DB when a Shazam-confirmed
release isn't in the Discogs catalog.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISCOVERED_DB_PATH = REPO_ROOT / "pi" / "data" / "discovered.sqlite"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS releases (
    mbid TEXT PRIMARY KEY,
    artist TEXT,
    title TEXT,
    year INTEGER,
    art_url TEXT,
    discogs_release_id INTEGER,
    discovered_at INTEGER
);
CREATE TABLE IF NOT EXISTS tracks (
    mbid TEXT NOT NULL,
    position TEXT NOT NULL,
    side TEXT,
    title TEXT,
    duration_seconds INTEGER,
    PRIMARY KEY (mbid, position),
    FOREIGN KEY (mbid) REFERENCES releases(mbid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tracks_mbid ON tracks(mbid);
CREATE TABLE IF NOT EXISTS negative_lookups (
    artist_norm TEXT NOT NULL,
    album_norm TEXT NOT NULL,
    stamped_at INTEGER NOT NULL,
    PRIMARY KEY (artist_norm, album_norm)
);
CREATE TABLE IF NOT EXISTS fp_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid TEXT NOT NULL,
    track_position TEXT NOT NULL,
    track_position_s REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(mbid, track_position, track_position_s)
);
CREATE INDEX IF NOT EXISTS idx_fp_refs_mbid ON fp_refs(mbid);
CREATE TABLE IF NOT EXISTS fp_hashes (
    hash TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    FOREIGN KEY(ref_id) REFERENCES fp_refs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fp_hashes_hash ON fp_hashes(hash);
CREATE INDEX IF NOT EXISTS idx_fp_hashes_ref ON fp_hashes(ref_id);
"""


def init_db(db_path: Path = DISCOVERED_DB_PATH) -> None:
    """Create the discovered-release schema if missing. Idempotent.

    Also runs the ``normalized_album`` migration: adds the column when
    missing, builds the (artist, normalized_album) index, and backfills
    legacy NULL rows with ``LOWER(TRIM(title))``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        # WAL keeps fingerprint reads concurrent with promotion writes,
        # mirroring fingerprint.db.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(_SCHEMA_SQL)
        _migrate_normalized_album(con)
        _migrate_clean_title(con)
        con.commit()


def _migrate_normalized_album(con: sqlite3.Connection) -> None:
    """Add ``releases.normalized_album`` when absent, build the index,
    and backfill NULL rows with ``LOWER(TRIM(title))``. Idempotent —
    runs on every boot via :func:`init_db`."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(releases)")}
    if "normalized_album" not in cols:
        con.execute("ALTER TABLE releases ADD COLUMN normalized_album TEXT")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_releases_artist_normalized_album "
        "ON releases(LOWER(artist), normalized_album)",
    )
    con.execute(
        "UPDATE releases SET normalized_album = LOWER(TRIM(title)) "
        "WHERE normalized_album IS NULL AND title IS NOT NULL",
    )


def _migrate_clean_title(con: sqlite3.Connection) -> None:
    """Add tracks.clean_title + clean_title_source when absent. Idempotent."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(tracks)")}
    if "clean_title" not in cols:
        con.execute("ALTER TABLE tracks ADD COLUMN clean_title TEXT")
    if "clean_title_source" not in cols:
        con.execute("ALTER TABLE tracks ADD COLUMN clean_title_source TEXT")


def set_track_duration_mbid(mbid: str, position: str, seconds: int) -> int:
    """Guarded write for discovered.sqlite (keyed by mbid). NULL-guarded.
    Returns rows updated (0 or 1). Used by ISRC-duration background
    enrichment; never overwrites an existing duration."""
    with sqlite3.connect(DISCOVERED_DB_PATH) as con:
        con.execute("PRAGMA busy_timeout=5000")
        cur = con.execute(
            "UPDATE tracks SET duration_seconds = ? "
            "WHERE mbid = ? AND position = ? AND duration_seconds IS NULL",
            (int(seconds), mbid, position),
        )
        con.commit()
        return cur.rowcount


def open_ro(db_path: Path = DISCOVERED_DB_PATH) -> sqlite3.Connection:
    """Open the discovered DB read-only. Returns row-factory connection.

    Raises ``sqlite3.OperationalError`` if the file doesn't exist —
    callers should call :func:`init_db` first or handle the error.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def open_rw(db_path: Path = DISCOVERED_DB_PATH) -> sqlite3.Connection:
    """Open the discovered DB read/write. Creates the file if missing."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con
