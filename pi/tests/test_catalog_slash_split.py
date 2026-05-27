"""Tests for the slash-split fallback in `catalog.find_by_artist_title`.

See feature shazam-medley-title-split. Background: when Shazam returns a
medley title like `"Changeling / Transmission"`, Discogs catalogs those
as two separate tracks, so reverse-lookup on the combined title returns
None. The fallback splits on ` / ` and retries each half, returning the
higher-scoring resolution.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.discogs import catalog  # noqa: E402


class _EmptyConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *_a, **_kw):
        class _C:
            def fetchall(self):
                return []
        return _C()


@pytest.fixture(autouse=True)
def _stub_db():
    """Suite-name artist-scan path (added after slash-split) reads the
    catalog DB when both primary + split miss. Stub it to empty so these
    tests don't need to mock open_ro individually."""
    with patch.object(catalog, "open_ro", lambda: _EmptyConn()):
        yield


def _primary_lookup(returns: dict[str, dict | None]):
    """Build a mock for `_find_by_artist_title_primary` that returns a
    crafted dict keyed by title (any other title -> None)."""

    def _impl(artist, title, *, preferred_release_id=None):  # skylos: ignore — test mock; inconsistent return is the emulated behavior
        result = returns.get(title)
        if result is None:
            return None
        # Honor sticky preference by bumping match_score if the caller
        # asked for a release this row happens to match.
        if (
            preferred_release_id is not None
            and result.get("id") == preferred_release_id
        ):
            out = dict(result)
            out["match_score"] = out.get("match_score", 0) + 25
            return out
        return result

    return _impl


def test_first_half_match():
    """Only the first half exists in the catalog → returns first half's release."""
    primary = _primary_lookup({
        "Changeling": {"id": 13820026, "match_score": 80, "title": "The Private Press"},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("DJ Shadow", "Changeling / Transmission")
    assert result is not None
    assert result["id"] == 13820026


def test_second_half_match():
    """Only the second half exists in the catalog → returns second half's release."""
    primary = _primary_lookup({
        "Transmission": {"id": 99999, "match_score": 80, "title": "Unknown Album"},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("DJ Shadow", "Changeling / Transmission")
    assert result is not None
    assert result["id"] == 99999


def test_higher_score_wins():
    """Both halves resolve to different releases at different scores → higher wins."""
    primary = _primary_lookup({
        "Changeling": {"id": 13820026, "match_score": 70, "title": "The Private Press"},
        "Transmission": {"id": 555, "match_score": 90, "title": "Joy Division 12\""},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("DJ Shadow", "Changeling / Transmission")
    assert result is not None
    assert result["id"] == 555


def test_tie_first_half_wins():
    """Equal score on both halves → first half wins (stable tie-breaker)."""
    primary = _primary_lookup({
        "Changeling": {"id": 13820026, "match_score": 80, "title": "The Private Press"},
        "Transmission": {"id": 555, "match_score": 80, "title": "Joy Division 12\""},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("DJ Shadow", "Changeling / Transmission")
    assert result is not None
    assert result["id"] == 13820026


def test_neither_half_matches():
    """Neither half is in the catalog → returns None."""
    primary = _primary_lookup({})
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("DJ Shadow", "Changeling / Transmission")
    assert result is None


def test_bare_slash_not_split():
    """Bare `/` without surrounding spaces is NOT split (e.g. `"S/T"`)."""
    calls: list[str] = []

    def _impl(artist, title, *, preferred_release_id=None):  # skylos: ignore — test mock; inconsistent return is the emulated behavior
        calls.append(title)
        return None

    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_impl):
        result = catalog.find_by_artist_title("Some Band", "S/T")
    assert result is None
    # Should only be called once (the primary lookup); no split attempted.
    assert calls == ["S/T"]


def test_no_slash_no_recursion():
    """Title without ` / ` and no primary match → returns None, no extra calls."""
    calls: list[str] = []

    def _impl(artist, title, *, preferred_release_id=None):  # skylos: ignore — test mock; inconsistent return is the emulated behavior
        calls.append(title)
        return None

    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_impl):
        result = catalog.find_by_artist_title("Some Band", "Hello")
    assert result is None
    assert calls == ["Hello"]


def test_sticky_preference_wins_via_bonus():
    """When one half matches preferred_release_id, sticky bonus tips score in its favor."""
    # Base scores equal; sticky bonus pushes the preferred release ahead.
    primary = _primary_lookup({
        "Changeling": {"id": 13820026, "match_score": 75, "title": "The Private Press"},
        "Transmission": {"id": 555, "match_score": 80, "title": "Joy Division 12\""},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title(
            "DJ Shadow",
            "Changeling / Transmission",
            preferred_release_id=13820026,
        )
    assert result is not None
    # Sticky bonus (+25) lifts Changeling from 75 → 100, beating Transmission's 80.
    assert result["id"] == 13820026


def test_primary_match_short_circuits():
    """If primary lookup succeeds on the unsplit title, no split is attempted."""
    calls: list[str] = []

    def _impl(artist, title, *, preferred_release_id=None):  # skylos: ignore — test mock; inconsistent return is the emulated behavior
        calls.append(title)
        return {"id": 42, "match_score": 95, "title": "Some Album"}

    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=_impl):
        result = catalog.find_by_artist_title("Artist", "Changeling / Transmission")
    assert result is not None
    assert result["id"] == 42
    assert calls == ["Changeling / Transmission"]


def test_three_track_medley_cascading_split():
    """`"A / B / C"` splits on first ` / `: left=`"A"`, right=`"B / C"`.
    Right-side primary fails, its own slash-split fallback fires and
    resolves `"C"`. Confirms recursion terminates by length reduction."""
    primary = _primary_lookup({
        "C": {"id": 333, "match_score": 80, "title": "Album C"},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("Artist", "A / B / C")
    assert result is not None
    assert result["id"] == 333


def test_empty_half_skipped():
    """A degenerate title like `" / Transmission"` (empty first half) still resolves second half."""
    primary = _primary_lookup({
        "Transmission": {"id": 555, "match_score": 80, "title": "12\""},
    })
    with patch.object(catalog, "_find_by_artist_title_primary", side_effect=primary):
        result = catalog.find_by_artist_title("DJ Shadow", " / Transmission")
    assert result is not None
    assert result["id"] == 555
