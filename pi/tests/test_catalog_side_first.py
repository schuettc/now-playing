"""Tests for catalog._is_side_first_track + first_position_per_side.

See feature side-first-cumulative-numbering. The bug: the old regex
only matched literal "[A-D]1" positions, so cumulative-numbered
multi-LPs (D15 = first physical track on D side) lost the +15
side-first bonus while their per-side-numbered siblings (D1) kept it.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

# Make the `nowplaying` package importable when pytest runs from the
# `pi/` directory or the repo root.
_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.discogs import catalog  # noqa: E402


# ---- _is_side_first_track (helper logic, no DB) ---------------------


def test_per_side_first_track():
    """Per-side numbering: D1 is the first track of side D."""
    with patch.object(catalog, "first_position_per_side", return_value={"D": "D1"}):
        assert catalog._is_side_first_track(999, "D1") is True


def test_per_side_mid_track():
    """Per-side numbering: D3 is not the first track of side D."""
    with patch.object(catalog, "first_position_per_side", return_value={"D": "D1"}):
        assert catalog._is_side_first_track(999, "D3") is False


def test_cumulative_first_track():
    """Cumulative numbering: D15 IS the first track of side D when the
    catalog records D15 as the first D-side row inserted (= physical
    first track regardless of label)."""
    with patch.object(catalog, "first_position_per_side", return_value={"D": "D15"}):
        assert catalog._is_side_first_track(999, "D15") is True


def test_cumulative_mid_track():
    """Cumulative numbering: D16 is not the first track of side D."""
    with patch.object(catalog, "first_position_per_side", return_value={"D": "D15"}):
        assert catalog._is_side_first_track(999, "D16") is False


def test_none_position():
    """None position returns False without raising."""
    assert catalog._is_side_first_track(999, None) is False


def test_empty_position():
    """Empty string position returns False."""
    assert catalog._is_side_first_track(999, "") is False


def test_whitespace_only_position():
    """Whitespace-only position must not IndexError on the [0] access."""
    assert catalog._is_side_first_track(999, "   ") is False


def test_numeric_only_position():
    """A position with no alpha side (e.g. CD track '1') returns False."""
    with patch.object(catalog, "first_position_per_side", return_value={}):
        assert catalog._is_side_first_track(999, "1") is False


def test_empty_firsts():
    """When the catalog returns no first-positions for the release, returns False."""
    with patch.object(catalog, "first_position_per_side", return_value={}):
        assert catalog._is_side_first_track(999, "A1") is False


def test_position_matches_case_insensitive():
    """Position comparison is case-insensitive (catalog stores upper, but
    callers may pass mixed-case)."""
    with patch.object(catalog, "first_position_per_side", return_value={"D": "D15"}):
        assert catalog._is_side_first_track(999, "d15") is True


# ---- first_position_per_side (DB lookup, in-memory SQLite) ----------


@contextmanager
def _in_memory_db_with_tracks(rows: list[tuple[int, str, str]]):
    """Build a temporary in-memory SQLite DB seeded with the given track
    rows (release_id, position, side) and yield it. Inserts in the
    provided order so rowid follows that order — that's what the side-
    first detector relies on to find the physical first track."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE tracks ("
        "  release_id INTEGER NOT NULL,"
        "  position TEXT,"
        "  side TEXT,"
        "  title TEXT,"
        "  duration_seconds INTEGER,"
        "  PRIMARY KEY (release_id, position)"
        ")"
    )
    for release_id, position, side in rows:
        con.execute(
            "INSERT INTO tracks (release_id, position, side, title) "
            "VALUES (?, ?, ?, ?)",
            (release_id, position, side, f"track-{position}"),
        )
    con.commit()
    try:
        yield con
    finally:
        con.close()


def test_first_position_per_side_cumulative_numbering():
    """Cumulative-numbered release (D15, D16, D17 inserted in order):
    first D-side row is D15, so it's the side-first track."""
    catalog.first_position_per_side.cache_clear()
    with _in_memory_db_with_tracks([
        (31427573, "A1", "A"),
        (31427573, "B5", "B"),
        (31427573, "C10", "C"),
        (31427573, "D15", "D"),
        (31427573, "D16", "D"),
        (31427573, "D17", "D"),
    ]) as con:
        with patch.object(catalog, "open_ro", return_value=con):
            firsts = catalog.first_position_per_side(31427573)
    assert firsts == {"A": "A1", "B": "B5", "C": "C10", "D": "D15"}


def test_first_position_per_side_per_side_numbering():
    """Per-side numbered release (D1, D2, D3): first D-side row is D1."""
    catalog.first_position_per_side.cache_clear()
    with _in_memory_db_with_tracks([
        (28988032, "A1", "A"),
        (28988032, "B1", "B"),
        (28988032, "C1", "C"),
        (28988032, "D1", "D"),
        (28988032, "D2", "D"),
        (28988032, "D3", "D"),
    ]) as con:
        with patch.object(catalog, "open_ro", return_value=con):
            firsts = catalog.first_position_per_side(28988032)
    assert firsts == {"A": "A1", "B": "B1", "C": "C1", "D": "D1"}


def test_first_position_per_side_missing_release_returns_empty():
    """A release with no tracks rows returns an empty dict, not None."""
    catalog.first_position_per_side.cache_clear()
    with _in_memory_db_with_tracks([]) as con:
        with patch.object(catalog, "open_ro", return_value=con):
            firsts = catalog.first_position_per_side(404)
    assert firsts == {}


def test_first_position_per_side_handles_db_error():
    """When open_ro raises OperationalError, returns empty dict."""
    catalog.first_position_per_side.cache_clear()

    def _broken():
        raise sqlite3.OperationalError("DB unavailable")

    with patch.object(catalog, "open_ro", side_effect=_broken):
        firsts = catalog.first_position_per_side(31427573)
    assert firsts == {}


def test_first_position_per_side_skips_null_side():
    """Rows with side IS NULL (CD-style positions like '1', '2') are excluded."""
    catalog.first_position_per_side.cache_clear()
    with _in_memory_db_with_tracks([
        (777, "1", None),
        (777, "2", None),
        (777, "A1", "A"),
    ]) as con:
        with patch.object(catalog, "open_ro", return_value=con):
            firsts = catalog.first_position_per_side(777)
    assert firsts == {"A": "A1"}
