"""Tests for `_should_scrobble` — the Last.fm scrobble-eligibility rule.

Live bug 2026-05-27: track played for ~5 minutes never scrobbled because
its Discogs row had no per-track duration. `_should_scrobble` short-
circuited on `duration < 30` before considering the ≥240s elapsed leg.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.history.scrobble import (  # noqa: E402
    SCROBBLE_MIN_DURATION_S,
    SCROBBLE_MIN_ELAPSED_S,
    SCROBBLE_UNKNOWN_DURATION_MIN_S,
    _should_scrobble,
)


# ── Happy path: duration known ─────────────────────────────────────────


def test_short_track_not_eligible() -> None:
    """Tracks under 30s are never scrobbled per Last.fm policy."""
    assert _should_scrobble(elapsed=29, duration=29) is False


def test_known_duration_50_percent_played_eligible() -> None:
    """3:00 track, 1:31 heard — past 50% of 180s. Eligible."""
    assert _should_scrobble(elapsed=91, duration=180) is True


def test_known_duration_under_50_percent_and_under_240s_not_eligible() -> None:
    """3:00 track, 1:00 heard — under both 50% and 240s. Not eligible."""
    assert _should_scrobble(elapsed=60, duration=180) is False


def test_known_long_duration_240s_elapsed_eligible() -> None:
    """10:00 track, 4:00 heard — past 240s even though only 40% played. Eligible."""
    assert _should_scrobble(elapsed=240, duration=600) is True


def test_known_long_duration_239s_elapsed_not_eligible() -> None:
    """One second short of the 240s threshold; well under 50%. Not eligible."""
    assert _should_scrobble(elapsed=239, duration=600) is False


# ── Unknown duration: 120s threshold (loosened from 240s) ──────────────


def test_unknown_duration_120s_elapsed_eligible() -> None:
    """Boundary: exactly 120s heard with unknown duration — eligible."""
    assert _should_scrobble(elapsed=120, duration=0) is True


def test_unknown_duration_119s_elapsed_not_eligible() -> None:
    """One second short of the 120s unknown-duration threshold — not eligible."""
    assert _should_scrobble(elapsed=119, duration=0) is False


def test_unknown_duration_240s_elapsed_eligible() -> None:
    """Well above the 120s threshold; still eligible."""
    assert _should_scrobble(elapsed=240, duration=0) is True


def test_unknown_duration_239s_now_eligible() -> None:
    """239s with unknown duration is above the 120s threshold — eligible.
    (Previously required 240s; updated to reflect 120s fallback.)"""
    assert _should_scrobble(elapsed=239, duration=0) is True


def test_unknown_duration_negative_treated_as_unknown() -> None:
    """Defensive: a stray negative duration acts like missing data."""
    assert _should_scrobble(elapsed=SCROBBLE_UNKNOWN_DURATION_MIN_S, duration=-1) is True
    assert _should_scrobble(elapsed=SCROBBLE_UNKNOWN_DURATION_MIN_S - 1, duration=-1) is False


def test_unknown_duration_with_50_percent_alone_does_not_match() -> None:
    """The 50% rule needs duration. Without it, only the ≥120s rule applies —
    even if elapsed > 50% of some implied duration."""
    # 60s elapsed with unknown duration: would be 50% of a 120s track if
    # duration were known, but we don't know it, so we must wait for 120s.
    assert _should_scrobble(elapsed=60, duration=0) is False


# ── Boundary constants ─────────────────────────────────────────────────


def test_exactly_at_min_duration_with_50_percent_eligible() -> None:
    """30s track, 15s heard — exact 50%. Eligible."""
    assert _should_scrobble(
        elapsed=SCROBBLE_MIN_DURATION_S // 2,
        duration=SCROBBLE_MIN_DURATION_S,
    ) is True


def test_below_min_duration_unknown_path_not_triggered() -> None:
    """Track marked at 29s with elapsed=240s — short-track gate fires first."""
    assert _should_scrobble(elapsed=240, duration=29) is False
