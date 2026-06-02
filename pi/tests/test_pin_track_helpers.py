"""Unit tests for the pin-track helper functions extracted in Phase B-3.

These cover the small pure helpers (`_parse_pin_request_body`,
`_validate_pin_lock`, `_resolve_pin_match`, `_apply_pin_to_locked`)
that the `pin_track` handler now orchestrates over. The endpoint-level
behavior is still exercised by `test_pin_track_endpoint.py`; these
tests pin down the helpers in isolation so future refactors stay safe.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control.pin_track import (  # noqa: E402
    _apply_pin_state,
    _apply_pin_to_locked,
    _parse_pin_request_body,
    _resolve_pin_match,
    _validate_pin_lock,
)


# ── _parse_pin_request_body ──────────────────────────────────────────


def test_parse_pin_request_body_happy() -> None:
    rid, pos = _parse_pin_request_body(
        {"release_id": 42, "track_position": "B2"},
    )
    assert rid == 42
    assert pos == "B2"


def test_parse_pin_request_body_coerces_str_id() -> None:
    rid, pos = _parse_pin_request_body(
        {"release_id": "99", "track_position": "A1"},
    )
    assert rid == 99
    assert pos == "A1"


def test_parse_pin_request_body_coerces_int_position() -> None:
    rid, pos = _parse_pin_request_body(
        {"release_id": 7, "track_position": 3},
    )
    assert rid == 7
    assert pos == "3"


@pytest.mark.parametrize("body", [
    {},
    {"release_id": 1},
    {"track_position": "A1"},
])
def test_parse_pin_request_body_missing_field(body) -> None:
    with pytest.raises(KeyError):
        _parse_pin_request_body(body)


def test_parse_pin_request_body_bad_int() -> None:
    with pytest.raises(ValueError):
        _parse_pin_request_body(
            {"release_id": "not-an-int", "track_position": "A1"},
        )


def test_parse_pin_request_body_none_position() -> None:
    # None coerces via str() to "None" — but a None release_id raises TypeError.
    with pytest.raises(TypeError):
        _parse_pin_request_body(
            {"release_id": None, "track_position": "A1"},
        )


# ── _validate_pin_lock ───────────────────────────────────────────────


def _state(last_vinyl):
    s = MagicMock()
    s.last_vinyl = last_vinyl
    return s


def test_validate_pin_lock_ok() -> None:
    s = _state({"release_id": 100})
    assert _validate_pin_lock(s, 100) is None


def test_validate_pin_lock_no_album() -> None:
    s = _state(None)
    resp = _validate_pin_lock(s, 100)
    assert resp is not None
    assert resp.status == 400


def test_validate_pin_lock_missing_release_id() -> None:
    s = _state({"artist": "x"})
    resp = _validate_pin_lock(s, 100)
    assert resp is not None
    assert resp.status == 400


def test_validate_pin_lock_mismatch() -> None:
    s = _state({"release_id": 100})
    resp = _validate_pin_lock(s, 200)
    assert resp is not None
    assert resp.status == 400


def test_validate_pin_lock_coerces_string_rid() -> None:
    # Locked rid stored as string is still accepted when it numerically matches.
    s = _state({"release_id": "100"})
    assert _validate_pin_lock(s, 100) is None


# ── _resolve_pin_match ───────────────────────────────────────────────


def _locked_state(tracklist):
    s = MagicMock()
    s.last_vinyl = {"release_id": 100, "tracklist": tracklist}
    return s


def test_resolve_pin_match_happy() -> None:
    s = _locked_state([
        {"position": "A1", "title": "Song A"},
        {"position": "B2", "title": "Song B"},
    ])
    matched, err = _resolve_pin_match(s, 100, "b2")
    assert err is None
    assert matched is not None
    assert matched["title"] == "Song B"


def test_resolve_pin_match_no_tracklist() -> None:
    s = MagicMock()
    s.last_vinyl = {"release_id": 100}
    # `from nowplaying.control import pin_track` resolves to the function
    # (re-exported in control/__init__.py), shadowing the submodule, so we
    # patch via the fully-qualified dotted path instead of patch.object.
    with patch("nowplaying.control.pin_track.discogs_catalog.get_release", return_value=None):
        matched, err = _resolve_pin_match(s, 100, "A1")
    assert matched is None
    assert err is not None
    assert err.status == 400


def test_resolve_pin_match_position_not_found() -> None:
    # Inline tracklist doesn't contain Z9; catalog also returns None →
    # position-not-in-tracklist 400.
    s = _locked_state([{"position": "A1", "title": "Only Track"}])
    with patch("nowplaying.control.pin_track.discogs_catalog.get_release", return_value=None):
        matched, err = _resolve_pin_match(s, 100, "Z9")
    assert matched is None
    assert err is not None
    assert err.status == 400


def test_resolve_pin_match_catalog_fallback_when_inline_incomplete() -> None:
    """Regression: predicted-advance leaves only the active track in inline tracklist.

    After predicted-advance flips state.last_vinyl to C11, the inline tracklist
    only contains the C11 entry. A pin request for C10 must fall back to the
    Discogs catalog (which has the full release) and resolve successfully.
    """
    # Inline only has the track predicted-advance promoted (C11 = Leo)
    s = _locked_state([{"position": "C11", "title": "Leo", "duration_seconds": 200}])
    # Catalog has the full release including C10 (Pitiful)
    catalog_release = {
        "tracks": [
            {"position": "C10", "title": "Pitiful", "duration_seconds": 240},
            {"position": "C11", "title": "Leo", "duration_seconds": 200},
            {"position": "C12", "title": "Segue 3", "duration_seconds": 60},
        ],
    }
    with patch(
        "nowplaying.control.pin_track.discogs_catalog.get_release",
        return_value=catalog_release,
    ):
        matched, err = _resolve_pin_match(s, 100, "C10")
    assert err is None
    assert matched is not None
    assert matched["position"] == "C10"
    assert matched["title"] == "Pitiful"


# ── _apply_pin_to_locked ─────────────────────────────────────────────


def test_apply_pin_to_locked_full_overlay() -> None:
    locked = {
        "release_id": 100,
        "artist": "DJ Shadow",
        "album": "Endtroducing",
        "track_position": "A1",
        "title": "Old Title",
        "duration_seconds": 999,
        "side": "A",
    }
    matched = {
        "position": "b2",
        "title": "New Title",
        "duration_seconds": 240,
        "side": "B",
    }
    canonical, title, duration = _apply_pin_to_locked(
        locked, matched, "b2", "2026-05-15T12:00:00Z",
    )
    assert canonical == "B2"
    assert title == "New Title"
    assert duration == 240
    # Identity fields preserved.
    assert locked["release_id"] == 100
    assert locked["artist"] == "DJ Shadow"
    assert locked["album"] == "Endtroducing"
    # Overlay applied.
    assert locked["track_position"] == "B2"
    assert locked["title"] == "New Title"
    assert locked["duration_seconds"] == 240
    assert locked["side"] == "B"
    assert locked["match_method"] == "user-identified"
    assert locked["match_confidence"] == "user"
    assert locked["ts"] == "2026-05-15T12:00:00Z"


def test_apply_pin_to_locked_clears_stale_duration() -> None:
    locked = {"release_id": 100, "duration_seconds": 500, "title": "old"}
    matched = {"position": "C2", "title": "Unknown Duration"}  # no duration
    _, _, duration = _apply_pin_to_locked(
        locked, matched, "C2", "ts",
    )
    assert duration is None
    assert "duration_seconds" not in locked


def test_apply_pin_to_locked_preserves_side_when_matched_missing() -> None:
    locked = {"release_id": 100, "side": "A"}
    matched = {"position": "A1", "title": "Song"}  # no side
    _apply_pin_to_locked(locked, matched, "A1", "ts")
    # Side is only overwritten when matched provides one.
    assert locked["side"] == "A"


def test_apply_pin_to_locked_uses_request_pos_when_matched_lacks_position() -> None:
    locked = {"release_id": 100}
    matched = {"title": "Song"}  # no position key
    canonical, _, _ = _apply_pin_to_locked(
        locked, matched, "  a1 ", "ts",
    )
    assert canonical == "A1"
    assert locked["track_position"] == "A1"


# ── _apply_pin_state (scaling-lock plumbing) ─────────────────────────


def _patched_loop():
    """Patch asyncio.get_running_loop so _apply_pin_state's confidence-stamp
    refresh works without a real loop."""
    fake_loop = MagicMock()
    fake_loop.time.return_value = 1_000_000.0
    return patch.object(asyncio, "get_running_loop", lambda: fake_loop)


def _pin_state(locked, *, user_track_pin=None, last_pin_unix_ts=None):
    """Minimal state stub for _apply_pin_state. The scaling-lock model derives
    the hold inside _apply_user_track_pin (mocked here), so this stub only
    needs the fields _apply_pin_state itself reads/captures."""
    s = MagicMock()
    s.last_vinyl = locked
    s.predicted_position = {"track_position": "C99"}  # stale; must be cleared
    s.last_pin_unix_ts = last_pin_unix_ts
    s.user_track_pin = user_track_pin
    return s


def test_apply_pin_state_clears_prediction_and_plumbs_applied_ttl() -> None:
    """_apply_pin_state reports the TTL the pin actually received (read back
    from state.user_track_pin after _apply_user_track_pin), not a separately
    recomputed value — and clears any stale prediction."""
    locked = {
        "release_id": 100, "track_position": "C10", "side": "C",
        "tracklist": [{"position": "C11", "duration_seconds": 200}],
    }
    matched = {"position": "C11", "title": "Leo", "duration_seconds": 200, "side": "C"}

    def _fake_apply(state, rid, pos, m):
        state.user_track_pin = {"duration_seconds": 155}

    state = _pin_state(locked)
    with _patched_loop(), patch(
        "nowplaying.control.pin_track._apply_user_track_pin", side_effect=_fake_apply,
    ):
        result = _apply_pin_state(state, locked, 100, matched, "C11")
    assert result.pin_ttl_seconds == 155
    assert result.canonical_pos == "C11"
    assert state.predicted_position is None


def test_apply_pin_state_captures_prior_pin_before_clobber() -> None:
    """The prior pin's unix ts and duration are captured BEFORE
    _apply_user_track_pin runs, so the backfill scheduler sees the prior
    values rather than the just-applied pin's."""
    locked = {
        "release_id": 100, "track_position": "C10", "side": "C",
        "tracklist": [{"position": "C11", "duration_seconds": 200}],
    }
    matched = {"position": "C11", "title": "Leo", "duration_seconds": 200, "side": "C"}
    state = _pin_state(
        locked, user_track_pin={"duration_seconds": 99}, last_pin_unix_ts=4242,
    )
    with _patched_loop(), patch("nowplaying.control.pin_track._apply_user_track_pin"):
        result = _apply_pin_state(state, locked, 100, matched, "C11")
    assert result.prior_pin_unix_ts == 4242
    assert result.prior_pin_duration_seconds == 99


def test_apply_pin_state_passes_no_backdate_to_pin() -> None:
    """The scaling lock must not be handed a dead-reckoned backdate:
    _apply_pin_state calls _apply_user_track_pin positionally with no
    reliable_position_s / track_started_at_iso kwargs."""
    locked = {
        "release_id": 100, "track_position": "C10", "side": "C",
        "tracklist": [{"position": "C11", "duration_seconds": 200}],
    }
    matched = {"position": "C11", "title": "Leo", "duration_seconds": 200, "side": "C"}
    state = _pin_state(locked)
    with _patched_loop(), patch(
        "nowplaying.control.pin_track._apply_user_track_pin",
    ) as mock_pin:
        _apply_pin_state(state, locked, 100, matched, "C11")
    _, kwargs = mock_pin.call_args
    assert "track_started_at_iso" not in kwargs
    assert "reliable_position_s" not in kwargs
