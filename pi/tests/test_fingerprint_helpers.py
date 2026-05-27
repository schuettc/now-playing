"""Unit tests for the small helpers extracted from fingerprint.match().

These cover the pure / SQL helpers in isolation so future refactors of
match() can't silently break the alignment-voting or hydration logic.
The end-to-end behaviour is still exercised by test_fingerprint.py;
these tests sit one layer below that.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

import pytest

from nowplaying.vinyl import fingerprint as fp


# ── _fetch_ref_hash_rows ────────────────────────────────────────────────


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """A fingerprint DB pre-loaded with two refs for release 100 and
    one ref for release 200, each with a handful of distinct hashes."""
    db = tmp_path / "fp.db"
    fp.init_db(db)
    conn = sqlite3.connect(db, isolation_level=None)
    try:  # skylos: ignore SKY-L004 — Why: large try is intentional to ensure conn.close() in finally regardless of any INSERT failure; standard test-fixture pattern
        # Release 100 / track A
        conn.execute(
            "INSERT INTO fp_refs (release_id, track_position, track_position_s, created_at)"
            " VALUES (?, ?, ?, ?)",
            (100, "A1", 0.0, "2026-01-01T00:00:00+00:00"),
        )
        ref_a = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Release 100 / track B
        conn.execute(
            "INSERT INTO fp_refs (release_id, track_position, track_position_s, created_at)"
            " VALUES (?, ?, ?, ?)",
            (100, "B1", 30.0, "2026-01-01T00:00:00+00:00"),
        )
        ref_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Release 200 / track C — should NEVER appear in release_filter=100 results
        conn.execute(
            "INSERT INTO fp_refs (release_id, track_position, track_position_s, created_at)"
            " VALUES (?, ?, ?, ?)",
            (200, "C1", 0.0, "2026-01-01T00:00:00+00:00"),
        )
        ref_c = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Hashes — overlapping query/ref hashes per design.
        rows = [
            ("hashA", ref_a, 0), ("hashA", ref_a, 5),
            ("hashB", ref_a, 10),
            ("hashB", ref_b, 100),
            ("hashC", ref_b, 200),
            ("hashA", ref_c, 0),  # belongs to release 200; must be filtered out
        ]
        conn.executemany(
            "INSERT INTO fp_hashes (hash, ref_id, offset) VALUES (?, ?, ?)", rows,
        )
    finally:
        conn.close()
    return db


def test_fetch_ref_hash_rows_filters_by_release(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        rows = fp._fetch_ref_hash_rows(conn, 100, ["hashA", "hashB", "hashC"])
    finally:
        conn.close()
    # release 200's ref_c row for hashA must be excluded.
    release_ids_in_rows = {r[0] for r in rows}
    # No row from ref_c (the release-200 ref) should appear; we don't
    # know its ref_id here but we know it must not match the two
    # release-100 ref ids.
    assert len(rows) == 5
    assert len(release_ids_in_rows) == 2


def test_fetch_ref_hash_rows_empty_query(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        rows = fp._fetch_ref_hash_rows(conn, 100, [])
    finally:
        conn.close()
    assert rows == []


def test_fetch_ref_hash_rows_chunks_large_query(monkeypatch, seeded_db: Path) -> None:
    """Force tiny chunk size so we exercise the chunking branch."""
    monkeypatch.setattr(fp, "_SQLITE_VAR_CHUNK", 2)
    conn = sqlite3.connect(seeded_db)
    try:
        rows = fp._fetch_ref_hash_rows(
            conn, 100, ["hashA", "hashB", "hashC", "missing1", "missing2"],
        )
    finally:
        conn.close()
    # Still 5 release-100 rows; chunking is transparent.
    assert len(rows) == 5


# ── _score_ref_alignments ───────────────────────────────────────────────


def test_score_ref_alignments_picks_dominant_delta() -> None:
    # ref 1: three hits all aligning at q_off - ref_off = +5 → score 3.
    # ref 2: two hits at delta=+5, one at delta=+10 → score 2.
    rows = [
        (1, "h1", 0), (1, "h2", 10), (1, "h3", 20),
        (2, "h1", 0), (2, "h2", 10), (2, "h3", 15),
    ]
    q_offsets: dict[str, list[int]] = defaultdict(list)
    q_offsets["h1"].append(5)
    q_offsets["h2"].append(15)
    q_offsets["h3"].append(25)
    scores = fp._score_ref_alignments(rows, q_offsets, min_hits=2)
    # Sorted by score desc; ref 1 first.
    assert scores == [(1, 3), (2, 2)]


def test_score_ref_alignments_respects_min_hits() -> None:
    rows = [(1, "h1", 0), (2, "h1", 0)]
    q_offsets: dict[str, list[int]] = defaultdict(list)
    q_offsets["h1"].append(0)
    scores = fp._score_ref_alignments(rows, q_offsets, min_hits=2)
    assert scores == []


def test_score_ref_alignments_multiple_query_offsets_per_hash() -> None:
    # Same hash appears twice on the query side. Each ref-side hit
    # should vote against both query offsets.
    rows = [(1, "h1", 0)]
    q_offsets: dict[str, list[int]] = defaultdict(list)
    q_offsets["h1"].extend([0, 5])
    scores = fp._score_ref_alignments(rows, q_offsets, min_hits=1)
    # The dominant delta gets 1 vote (each delta is distinct).
    assert scores == [(1, 1)]


# ── _hydrate_top_refs ───────────────────────────────────────────────────


def test_hydrate_top_refs_returns_hits_in_score_order(seeded_db: Path) -> None:
    # Look up ref_a (score 7) and ref_b (score 3) — score order preserved.
    conn = sqlite3.connect(seeded_db)
    try:
        # IDs from the fixture: ref_a=1, ref_b=2 (auto-inc).
        hits = fp._hydrate_top_refs(conn, [(1, 7), (2, 3)])
    finally:
        conn.close()
    assert len(hits) == 2
    assert hits[0].ref_id == 1
    assert hits[0].hits == 7
    assert hits[0].release_id == 100
    assert hits[0].track_position == "A1"
    assert hits[1].ref_id == 2
    assert hits[1].hits == 3
    assert hits[1].track_position == "B1"


def test_hydrate_top_refs_skips_missing_refs(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        # ID 9999 doesn't exist; valid id 1 should still come back.
        hits = fp._hydrate_top_refs(conn, [(1, 5), (9999, 4)])
    finally:
        conn.close()
    assert len(hits) == 1
    assert hits[0].ref_id == 1


# ── _insert_ref_with_hashes ─────────────────────────────────────────────


def test_insert_ref_with_hashes_inserts_new_row(tmp_path: Path) -> None:
    db = tmp_path / "fp.db"
    fp.init_db(db)
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        ref_id = fp._insert_ref_with_hashes(
            cur, 42, "A1", 0.0, "2026-01-01T00:00:00+00:00",
            [("h1", 0), ("h2", 5)],
        )
        cur.execute("COMMIT")
        # Two hashes recorded for the new ref.
        n = conn.execute(
            "SELECT COUNT(*) FROM fp_hashes WHERE ref_id = ?", (ref_id,),
        ).fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_insert_ref_with_hashes_idempotent_on_existing(tmp_path: Path) -> None:
    db = tmp_path / "fp.db"
    fp.init_db(db)
    conn = sqlite3.connect(db, isolation_level=None)
    try:  # skylos: ignore SKY-L004 — Why: large try ensures conn.close() in finally on any assertion failure; standard test-fixture pattern
        cur = conn.cursor()
        cur.execute("BEGIN")
        first = fp._insert_ref_with_hashes(
            cur, 42, "A1", 0.0, "2026-01-01T00:00:00+00:00",
            [("h1", 0)],
        )
        cur.execute("COMMIT")
        cur.execute("BEGIN")
        second = fp._insert_ref_with_hashes(
            cur, 42, "A1", 0.0, "2026-01-01T00:00:00+00:00",
            [("h2", 0)],  # should NOT be inserted because ref already exists
        )
        cur.execute("COMMIT")
        assert first == second
        # Only the first batch's hash was stored.
        hashes = conn.execute(
            "SELECT hash FROM fp_hashes WHERE ref_id = ?", (first,),
        ).fetchall()
        assert [h[0] for h in hashes] == ["h1"]
    finally:
        conn.close()
