"""MBID-keyed fingerprint store for discovered (non-Discogs) releases.

Parallel to ``nowplaying.vinyl.fingerprint`` (Discogs-keyed by integer
``release_id``), but writes into ``pi/data/discovered.sqlite``'s
``fp_refs`` / ``fp_hashes`` tables keyed on MusicBrainz ``mbid`` (TEXT).

Reuses the algorithm helpers from ``nowplaying.vinyl.fingerprint``
(``_fingerprint``, ``_score_ref_alignments``) — only the SQL layer and
hydration shape are MBID-specific.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from nowplaying.discovery.schema import DISCOVERED_DB_PATH
from nowplaying.vinyl.fingerprint import (
    Hit,
    _fingerprint,
    _score_ref_alignments,
)

_DB_LOCK = threading.Lock()

# Mirror ``_SQLITE_VAR_CHUNK`` from the Discogs cascade — same SQLite
# variable-count ceiling applies here.
_SQLITE_VAR_CHUNK = 800


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _insert_ref_with_hashes(
    cur: sqlite3.Cursor,
    mbid: str,
    track_position: str,
    track_position_s: float,
    now: str,
    fingerprint_data: list[tuple[str, int]],
) -> int:
    cur.execute(
        """
        INSERT OR IGNORE INTO fp_refs
          (mbid, track_position, track_position_s, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (mbid, track_position, track_position_s, now),
    )
    if cur.rowcount == 0:
        row = cur.execute(
            """
            SELECT id FROM fp_refs
            WHERE mbid = ? AND track_position = ? AND track_position_s = ?
            """,
            (mbid, track_position, track_position_s),
        ).fetchone()
        return int(row[0])
    ref_id = int(cur.lastrowid)
    if fingerprint_data:
        cur.executemany(
            "INSERT INTO fp_hashes (hash, ref_id, offset) VALUES (?, ?, ?)",
            [(h, ref_id, off) for h, off in fingerprint_data],
        )
    return ref_id


def add_ref(
    mbid: str,
    track_position: str,
    track_position_s: float,
    wav_bytes: bytes,
    db_path: Path = DISCOVERED_DB_PATH,
) -> int:
    """Fingerprint a clip and store as a ref keyed on ``mbid``.

    Returns the new ref_id, or the existing ref_id if a row with the same
    (mbid, track_position, track_position_s) already exists.
    """
    fingerprint_data = _fingerprint(wav_bytes)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _DB_LOCK, contextlib.closing(_connect(db_path)) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        try:
            ref_id = _insert_ref_with_hashes(
                cur, mbid, track_position, track_position_s,
                now, fingerprint_data,
            )
        except Exception:
            cur.execute("ROLLBACK")
            raise
        cur.execute("COMMIT")
        return ref_id


def _fetch_ref_hash_rows_scoped(
    conn: sqlite3.Connection,
    mbid_filter: str,
    query_hashes: list[str],
) -> list[tuple]:
    rows: list[tuple] = []
    for i in range(0, len(query_hashes), _SQLITE_VAR_CHUNK):
        chunk = query_hashes[i:i + _SQLITE_VAR_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        query = (  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(chunk); all real values flow through bound parameters below
            f"SELECT fh.ref_id, fh.hash, fh.offset "
            f"FROM fp_hashes fh "
            f"JOIN fp_refs fr ON fr.id = fh.ref_id "
            f"WHERE fr.mbid = ? AND fh.hash IN ({placeholders})"
        )
        rows.extend(conn.execute(query, (mbid_filter, *chunk)).fetchall())
    return rows


def _fetch_ref_hash_rows_blind(
    conn: sqlite3.Connection,
    query_hashes: list[str],
) -> list[tuple]:
    rows: list[tuple] = []
    for i in range(0, len(query_hashes), _SQLITE_VAR_CHUNK):
        chunk = query_hashes[i:i + _SQLITE_VAR_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        query = (  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(chunk); all real values flow through bound parameters below
            f"SELECT fh.ref_id, fh.hash, fh.offset "
            f"FROM fp_hashes fh "
            f"WHERE fh.hash IN ({placeholders})"
        )
        rows.extend(conn.execute(query, chunk).fetchall())
    return rows


def _hydrate_top_refs(
    conn: sqlite3.Connection,
    ref_scores: list[tuple[int, int]],
) -> list[Hit]:
    """Hydrate MBID-keyed fp_refs rows into ``Hit`` objects.

    Sets ``mbid`` and leaves ``release_id`` as ``None`` to signal the
    discovered store as the source. Mirrors the shape of
    ``nowplaying.vinyl.fingerprint._hydrate_top_refs`` but reads ``mbid``
    instead of ``release_id`` out of fp_refs.
    """
    ref_ids = [r for r, _ in ref_scores]
    placeholders = ",".join("?" * len(ref_ids))
    query = (  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(ref_ids); all real values flow through bound parameters below
        f"SELECT id, mbid, track_position, track_position_s "
        f"FROM fp_refs WHERE id IN ({placeholders})"
    )
    meta_rows = conn.execute(query, ref_ids).fetchall()
    meta = {r[0]: r for r in meta_rows}
    hits: list[Hit] = []
    for ref_id, score in ref_scores:
        m = meta.get(ref_id)
        if m is None:
            continue
        hits.append(Hit(
            ref_id=int(m[0]),
            release_id=None,
            track_position=str(m[2]),
            hits=score,
            track_position_s=float(m[3]),
            mbid=str(m[1]),
        ))
    return hits


def match(
    wav_bytes: bytes,
    mbid_filter: str | None,
    min_hits: int = 10,
    db_path: Path = DISCOVERED_DB_PATH,
) -> list[Hit]:
    """Match a clip against discovered fp_refs.

    ``mbid_filter``:
    - ``str`` — scoped confirmation scan: only refs for that mbid.
    - ``None`` — blind scan across every discovered ref.
    """
    if not db_path.exists():
        return []
    query_fp = _fingerprint(wav_bytes)
    if not query_fp:
        return []
    query_offsets_by_hash: dict[str, list[int]] = defaultdict(list)
    for h, off in query_fp:
        query_offsets_by_hash[h].append(off)
    query_hashes = list(query_offsets_by_hash.keys())
    if not query_hashes:
        return []
    with contextlib.closing(_connect(db_path)) as conn:
        if mbid_filter is None:
            rows = _fetch_ref_hash_rows_blind(conn, query_hashes)
        else:
            rows = _fetch_ref_hash_rows_scoped(conn, mbid_filter, query_hashes)
        ref_scores = _score_ref_alignments(rows, query_offsets_by_hash, min_hits)
        if not ref_scores:
            return []
        return _hydrate_top_refs(conn, ref_scores)


def delete_refs(ref_ids: list[int], db_path: Path = DISCOVERED_DB_PATH) -> int:
    """Delete refs by id. fp_hashes rows cascade via FK."""
    if not ref_ids:
        return 0
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            placeholders = ",".join("?" * len(ref_ids))
            query = f"DELETE FROM fp_refs WHERE id IN ({placeholders})"  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(ref_ids); all real values flow through bound parameters below
            cur = conn.execute(query, ref_ids)
            return cur.rowcount
        finally:
            conn.close()
