"""Tests for the album-stats session counting and the new
MIN_TRACKS_PER_SESSION / current-session-exclusion filters.

The plays come in as plain tuples — `_count_album_sessions` and friends
accept any positional triple (started, ended, track_position), so we
don't need to round-trip through sqlite for these.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import history  # noqa: E402


# Reference instant for tests: well after any synthetic timestamps.
NOW = 1_800_000_000  # ~ 2027


def _row(started: int, ended: int, track: int | None) -> tuple:
    return (started, ended, track)


# ---------------------------------------------------------------------------
# _bucket_album_sessions
# ---------------------------------------------------------------------------

def test_bucket_groups_by_gap():
    """Two rows within the 30-min gap → one session; > 30 min apart →
    two sessions."""
    rows = [
        _row(1000, 1100, 1),
        _row(1200, 1300, 2),                 # 100s gap → same session
        _row(1300 + 31 * 60, 1300 + 31 * 60 + 100, 3),  # > 30 min later
    ]
    sessions = history._bucket_album_sessions(rows)
    assert len(sessions) == 2
    assert sessions[0]["tracks"] == {1, 2}
    assert sessions[1]["tracks"] == {3}


def test_bucket_distinct_track_set():
    """Repeated track within a session → counted once in the set."""
    rows = [
        _row(1000, 1100, 5),
        _row(1200, 1300, 5),  # same track replayed
        _row(1400, 1500, 7),
    ]
    sessions = history._bucket_album_sessions(rows)
    assert sessions[0]["tracks"] == {5, 7}


def test_bucket_treats_null_as_one_unknown_track():
    """Multiple None track_positions inside a session collapse to one
    bucket — conservative."""
    rows = [_row(1000, 1100, None), _row(1200, 1300, None)]
    sessions = history._bucket_album_sessions(rows)
    assert sessions[0]["tracks"] == {None}


# ---------------------------------------------------------------------------
# _qualifying_sessions
# ---------------------------------------------------------------------------

def test_short_session_does_not_qualify():
    """Two tracks of an album, played long ago — doesn't reach the
    3-track minimum, so it's filtered out."""
    rows = [_row(NOW - 7 * 86400, NOW - 7 * 86400 + 200, 1),
            _row(NOW - 7 * 86400 + 300, NOW - 7 * 86400 + 500, 2)]
    qualifying = history._qualifying_sessions(rows, now=NOW)
    assert qualifying == []


def test_three_track_session_qualifies():
    """Exactly 3 distinct tracks in a session is the threshold."""
    base = NOW - 7 * 86400
    rows = [_row(base, base + 200, 1),
            _row(base + 300, base + 500, 2),
            _row(base + 600, base + 800, 3)]
    qualifying = history._qualifying_sessions(rows, now=NOW)
    assert len(qualifying) == 1
    assert qualifying[0]["tracks"] == {1, 2, 3}


def test_current_session_is_excluded():
    """A qualifying session that's still in progress (last ended_at
    within 30 min of now) doesn't count yet — user is listening RIGHT
    NOW and we shouldn't claim 'Played N+1 times' mid-session."""
    rows = [
        _row(NOW - 600, NOW - 500, 1),
        _row(NOW - 400, NOW - 300, 2),
        _row(NOW - 200, NOW - 100, 3),
    ]
    qualifying = history._qualifying_sessions(rows, now=NOW)
    assert qualifying == []


def test_current_session_excluded_prior_session_counted():
    """Past completed session counts; in-progress current session
    doesn't bump the count until it ends."""
    # Past completed session: 7 days ago, 4 distinct tracks
    base = NOW - 7 * 86400
    past = [
        _row(base, base + 200, 1),
        _row(base + 300, base + 500, 2),
        _row(base + 600, base + 800, 3),
        _row(base + 900, base + 1100, 4),
    ]
    # Current session: now-ish, 3 tracks (would qualify if completed)
    current = [
        _row(NOW - 600, NOW - 500, 5),
        _row(NOW - 400, NOW - 300, 6),
        _row(NOW - 200, NOW - 100, 7),
    ]
    qualifying = history._qualifying_sessions(past + current, now=NOW)
    assert len(qualifying) == 1
    assert qualifying[0]["tracks"] == {1, 2, 3, 4}


def test_playlist_drive_by_does_not_count():
    """The canonical case the user flagged: a streaming playlist
    happens to include one track from this album. The session lasts
    minutes, includes only 1 distinct track from the album, and
    shouldn't bump the album's play count."""
    base = NOW - 14 * 86400
    rows = [_row(base, base + 240, 4)]  # one 4-min play of track 4
    qualifying = history._qualifying_sessions(rows, now=NOW)
    assert qualifying == []


def test_min_tracks_kwarg_overrides_default():
    """The threshold is configurable for callers that want a different
    bar (e.g. EPs)."""
    base = NOW - 7 * 86400
    rows = [_row(base, base + 200, 1), _row(base + 300, base + 500, 2)]
    assert history._qualifying_sessions(
        rows, now=NOW, min_tracks=2,
    ) != []
    assert history._qualifying_sessions(
        rows, now=NOW, min_tracks=3,
    ) == []


# ---------------------------------------------------------------------------
# _count_album_sessions
# ---------------------------------------------------------------------------

def test_count_matches_qualifying_length():
    base = NOW - 7 * 86400
    rows = [
        # qualifying session A: 3 tracks
        _row(base, base + 200, 1),
        _row(base + 300, base + 500, 2),
        _row(base + 600, base + 800, 3),
        # short non-qualifying session B: 1 track, gap > 30min
        _row(base + 86400, base + 86400 + 200, 1),
    ]
    assert history._count_album_sessions(rows, now=NOW) == 1
