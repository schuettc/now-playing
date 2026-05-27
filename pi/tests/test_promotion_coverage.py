"""Tests for ``promotion.should_promote_for_coverage``.

Covers the spatial gap-detection logic independently of the orchestrator.
The function is purely a DB query: returns True when no ref exists within
``± spacing_s / 2`` of the requested position, False otherwise (ref nearby
or cohort cap reached).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nowplaying.vinyl import fingerprint, promotion


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "fp.db"
    fingerprint.init_db(db)
    return db


def _insert_ref(db: Path, release_id: int, track_position: str, pos_s: float) -> None:
    """Insert a minimal fp_refs row directly (no audio needed for these tests).

    ``should_promote_for_coverage`` only reads ``track_position_s`` from
    ``fp_refs``; hashes live in the separate ``fp_hashes`` table and are
    irrelevant here.
    """
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO fp_refs (release_id, track_position, track_position_s, created_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (release_id, track_position, pos_s),
        )


# ── Test 1 — cold cohort: no refs → always a gap ─────────────────────────


def test_cold_cohort_is_a_gap(tmp_db: Path) -> None:
    """No refs exist → should_promote_for_coverage returns True."""
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=30.0,
        db_path=tmp_db,
    )
    assert result is True


# ── Test 2 — ref within window → no gap ──────────────────────────────────


def test_ref_within_window_is_not_a_gap(tmp_db: Path) -> None:
    """Existing ref within ± spacing_s / 2 → returns False (gap already covered)."""
    _insert_ref(tmp_db, 100, "A1", 30.0)
    # Request position 35.0 with spacing_s=15 → window is [27.5, 42.5].
    # 30.0 falls inside → not a gap.
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=35.0,
        spacing_s=15.0,
        db_path=tmp_db,
    )
    assert result is False


# ── Test 3 — ref outside window → gap ────────────────────────────────────


def test_ref_outside_window_is_a_gap(tmp_db: Path) -> None:
    """Existing ref farther than ± spacing_s / 2 → returns True (gap)."""
    _insert_ref(tmp_db, 100, "A1", 10.0)
    # Request position 30.0 with spacing_s=15 → window is [22.5, 37.5].
    # 10.0 is outside → gap exists.
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=30.0,
        spacing_s=15.0,
        db_path=tmp_db,
    )
    assert result is True


# ── Test 4 — cohort cap reached → not a gap ──────────────────────────────


def test_cohort_cap_suppresses_promotion(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the cohort is at cap, should_promote_for_coverage returns False.

    ``duration_s=None`` so the cap comes from the ``_MIN_COHORT_CAP`` floor
    (monkeypatched to 3).  When ``duration_s`` is set, ``max_refs_for_duration``
    uses ``ceil(duration_s / 15)`` which may exceed the floor and bypass the
    monkeypatch.  Passing None forces the floor path.
    """
    monkeypatch.setattr(promotion, "_MIN_COHORT_CAP", 3)
    # Insert 3 refs at positions far from the target (so the window check
    # alone would pass, but the cap check fires first).
    for i in range(3):
        _insert_ref(tmp_db, 100, "A1", float(i * 100))
    # Request a position in a genuine gap — cap is the gate here.
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=250.0,
        spacing_s=15.0,
        duration_s=None,  # forces _MIN_COHORT_CAP floor
        db_path=tmp_db,
    )
    assert result is False


# ── Test 5 — boundary: ref at exactly half-window ────────────────────────


def test_boundary_at_half_window_is_not_a_gap(tmp_db: Path) -> None:
    """Ref at exactly ± spacing_s / 2 → window is inclusive, returns False."""
    spacing_s = 15.0
    half = spacing_s / 2.0  # 7.5
    _insert_ref(tmp_db, 100, "A1", 30.0)
    # target = 30.0 + 7.5 = 37.5 → distance == half_window → not a gap.
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=30.0 + half,
        spacing_s=spacing_s,
        db_path=tmp_db,
    )
    assert result is False


def test_boundary_just_outside_half_window_is_a_gap(tmp_db: Path) -> None:
    """Ref at > ± spacing_s / 2 → outside window, returns True (gap)."""
    spacing_s = 15.0
    half = spacing_s / 2.0  # 7.5
    epsilon = 0.001
    _insert_ref(tmp_db, 100, "A1", 30.0)
    # target = 30.0 + 7.5 + epsilon → distance > half_window → gap.
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=30.0 + half + epsilon,
        spacing_s=spacing_s,
        db_path=tmp_db,
    )
    assert result is True


# ── Test 6 — different cohort doesn't affect result ──────────────────────


def test_different_cohort_refs_are_ignored(tmp_db: Path) -> None:
    """Refs for a different track_position don't count for this cohort."""
    # Insert a ref for A2 near the target position.
    _insert_ref(tmp_db, 100, "A2", 30.0)
    # A1 cohort is empty → gap.
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="A1",
        track_position_s=30.0,
        spacing_s=15.0,
        db_path=tmp_db,
    )
    assert result is True


# ── Test 7 — empty track_position → False ────────────────────────────────


def test_empty_track_position_returns_false(tmp_db: Path) -> None:
    """Empty track_position has no cohort identity → False (skip)."""
    result = promotion.should_promote_for_coverage(
        release_id=100,
        track_position="",
        track_position_s=30.0,
        db_path=tmp_db,
    )
    assert result is False
