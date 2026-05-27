"""Database bootstrap and schema migration for the Discogs sync."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# pi/scripts/discogs/_db.py → repo root is 3 levels up
# (discogs → scripts → pi → <repo>). The prior parents[4] resolved to
# one level above the repo and silently created a phantom DB at
# /<homedir>/pi/data/discogs.sqlite instead of the real one — verified
# on the Pi 2026-05-26 when the suite-parent backfill scanned an empty
# DB and reported 0 releases to update.
REPO_ROOT = Path(__file__).resolve().parents[3]
PI_DIR = REPO_ROOT / "pi"
DATA_DIR = PI_DIR / "data"
DB_PATH = DATA_DIR / "discogs.sqlite"
ART_DIR = DATA_DIR / "art"

DDL = """
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY,
  artist TEXT,
  title TEXT,
  year INTEGER,
  country TEXT,
  format TEXT,
  label TEXT,
  catno TEXT,
  primary_image_url TEXT,
  art_path TEXT,
  raw_basic_json TEXT,
  raw_detail_json TEXT,
  basic_synced_at TEXT,
  detail_synced_at TEXT,
  art_synced_at TEXT,
  -- Cached MusicBrainz Identifier for this release, used as a fallback
  -- source for per-track durations when Discogs returns empty `duration`
  -- fields (see _enrich_durations_from_musicbrainz). Set once on first
  -- successful resolve; reused on subsequent syncs so we don't re-search.
  musicbrainz_mbid TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
  release_id INTEGER NOT NULL,
  position TEXT,
  side TEXT,
  title TEXT,
  duration_seconds INTEGER,
  -- 1 when this row is a suite/medley *parent* (e.g. ``D1, "Homecoming"``)
  -- whose playable movements live in child rows (``D1. I``, ``D1. II``…).
  -- Shazam returns the parent suite title when any movement is playing,
  -- so we store it here for reverse-lookup. get_release filters these
  -- out so downstream (state, kiosk, gates) sees only playable leaves.
  is_suite_parent INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (release_id, position),
  FOREIGN KEY (release_id) REFERENCES releases(id)
);

CREATE TABLE IF NOT EXISTS sync_state (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_releases_artist_title ON releases(artist, title);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}", flush=True)


def open_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    con.executescript(DDL)
    _migrate_schema(con)
    return con


def _migrate_schema(con: sqlite3.Connection) -> None:
    """Idempotent schema migrations for existing DBs that pre-date a new
    column. ALTER TABLE raises ``OperationalError: duplicate column name``
    when re-run; catching that lets ``open_db`` be called any number of
    times without fear.
    """
    for stmt in (
        "ALTER TABLE releases ADD COLUMN musicbrainz_mbid TEXT",
        "ALTER TABLE tracks ADD COLUMN is_suite_parent INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            con.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                continue
            raise
