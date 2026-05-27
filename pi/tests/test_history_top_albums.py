"""Tests for top_albums() data-quality fixes:

1. _is_corrupted_shazam_row  — filters album==title Shazam-only rows
2. _merge_null_release_groups — merges NULL release_id groups into non-NULL ones
3. Tie-breaking in _merge_null_release_groups with multiple non-NULL release_ids
4. Baseline — clean rows are unaffected by both filters
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import history  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    release_id: int | None,
    album: str,
    artist: str,
    match_method: str | None = None,
    title: str | None = None,
    started_at: int = 1_000_000,
    ended_at: int = 1_001_000,
) -> sqlite3.Row:
    """Build a sqlite3.Row-like MagicMock with the columns top_albums() uses."""
    row = MagicMock(spec=sqlite3.Row)
    row.__getitem__ = lambda self, key: {  # type: ignore[misc] — assigning __getitem__ on a MagicMock instance is valid at runtime; mypy flags lambda assignment to a dunder as misc but the test fixture pattern requires it
        "release_id": release_id,
        "album": album,
        "artist": artist,
        "match_method": match_method,
        "title": title,
        "started_at": started_at,
        "ended_at": ended_at,
    }[key]
    return row


# ---------------------------------------------------------------------------
# _is_corrupted_shazam_row
# ---------------------------------------------------------------------------

def test_corrupted_shazam_row_detected():
    """release_id=NULL, match_method='shazam', album==title → corrupted."""
    row = _make_row(None, "get behind this", "flor", match_method="shazam", title="get behind this")
    assert history._is_corrupted_shazam_row(row) is True


def test_corrupted_shazam_row_case_insensitive():
    """Case difference alone shouldn't let a corrupted row through."""
    row = _make_row(None, "Sin", "artist", match_method="shazam", title="sin")
    assert history._is_corrupted_shazam_row(row) is True


def test_legitimate_title_track_not_filtered():
    """Same album and title BUT a confirmed release_id → not corrupted."""
    row = _make_row(123, "Black Sabbath", "Black Sabbath", match_method="shazam", title="Black Sabbath")
    assert history._is_corrupted_shazam_row(row) is False


def test_different_match_method_not_filtered():
    """release_id=NULL and album==title but match_method != 'shazam' → keep."""
    row = _make_row(None, "Some Title", "Artist", match_method="sonos-didl", title="Some Title")
    assert history._is_corrupted_shazam_row(row) is False


def test_different_album_and_title_not_filtered():
    """album != title → normal row, not corrupted."""
    row = _make_row(None, "California Nights", "Best Coast", match_method="shazam", title="Feeling OK")
    assert history._is_corrupted_shazam_row(row) is False


def test_null_title_not_filtered():
    """title=NULL → album==title can't be evaluated; not corrupted."""
    row = _make_row(None, "Some Album", "Artist", match_method="shazam", title=None)
    assert history._is_corrupted_shazam_row(row) is False


# ---------------------------------------------------------------------------
# _merge_null_release_groups
# ---------------------------------------------------------------------------

def test_null_group_merged_into_non_null():
    """NULL group for same (album, artist) as a non-NULL group is folded in."""
    groups = {
        (None, "Santigold", "Santigold"): [(1000, 1100), (1200, 1300)],
        (123, "Santigold", "Santigold"): [(2000, 2100)],
    }
    merged = history._merge_null_release_groups(groups)

    assert (None, "Santigold", "Santigold") not in merged
    assert (123, "Santigold", "Santigold") in merged
    pairs = merged[(123, "Santigold", "Santigold")]
    # All three pairs present and sorted by started_at
    assert len(pairs) == 3
    assert pairs == sorted(pairs, key=lambda p: p[0])


def test_null_group_kept_when_no_non_null_match():
    """NULL group with no corresponding non-NULL group is preserved as-is."""
    groups = {
        (None, "Lost On You", "LP"): [(1000, 1100)],
    }
    merged = history._merge_null_release_groups(groups)
    assert (None, "Lost On You", "LP") in merged
    assert len(merged) == 1


def test_non_null_groups_unaffected_when_no_null():
    """Non-NULL groups without a NULL counterpart are untouched."""
    groups = {
        (1, "Hot Fuss", "The Killers"): [(1000, 1100), (2000, 2100)],
        (2, "The Suburbs", "Arcade Fire"): [(3000, 3100)],
    }
    merged = history._merge_null_release_groups(groups)
    assert merged == groups


def test_tiebreak_merges_into_highest_pair_count_group():
    """When two non-NULL release_ids exist, NULL pairs go to the one with more pairs."""
    groups = {
        (None, "Some Album", "Artist"): [(500, 600)],
        (10, "Some Album", "Artist"): [(1000, 1100), (2000, 2100)],  # 2 pairs
        (20, "Some Album", "Artist"): [(3000, 3100)],                 # 1 pair
    }
    merged = history._merge_null_release_groups(groups)

    assert (None, "Some Album", "Artist") not in merged
    # NULL pairs merged into rid=10 (2 pairs > 1 pair)
    pairs_10 = merged[(10, "Some Album", "Artist")]
    assert len(pairs_10) == 3  # 2 original + 1 NULL
    assert len(merged[(20, "Some Album", "Artist")]) == 1  # untouched


def test_tiebreak_uses_lowest_rid_on_equal_pair_count():
    """Equal pair counts → NULL pairs go to the group with the lowest release_id."""
    groups = {
        (None, "Some Album", "Artist"): [(500, 600)],
        (10, "Some Album", "Artist"): [(1000, 1100)],  # 1 pair
        (20, "Some Album", "Artist"): [(3000, 3100)],  # 1 pair (tie → lowest rid wins)
    }
    merged = history._merge_null_release_groups(groups)

    assert (None, "Some Album", "Artist") not in merged
    pairs_10 = merged[(10, "Some Album", "Artist")]
    assert len(pairs_10) == 2  # 1 original + 1 NULL
    assert len(merged[(20, "Some Album", "Artist")]) == 1  # untouched


def test_merged_pairs_are_sorted_chronologically():
    """After merging, the combined pair list is sorted ascending by started_at."""
    groups = {
        (None, "Album", "Artist"): [(500, 600), (100, 200)],   # out of order
        (1, "Album", "Artist"): [(300, 400), (700, 800)],
    }
    merged = history._merge_null_release_groups(groups)
    pairs = merged[(1, "Album", "Artist")]
    assert pairs == sorted(pairs, key=lambda p: p[0])


# ---------------------------------------------------------------------------
# Baseline: clean rows untouched
# ---------------------------------------------------------------------------

def test_clean_rows_not_corrupted():
    """Normal rows (release_id set, album != title) are never flagged."""
    row = _make_row(42, "Little Creatures", "Talking Heads", match_method="shazam", title="And She Was")
    assert history._is_corrupted_shazam_row(row) is False
