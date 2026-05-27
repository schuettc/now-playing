"""Tests for state.user_track_pin lifecycle.

Covers the pure _evaluate_user_pin helper plus the State-mutation
cleanup sites in main.py. See docs/features/manual-override-track-pin/.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.main import (  # noqa: E402
    PIN_DIFFERENT_TRACK_RELEASE_STREAK,
    PIN_TTL_BUFFER_S,
    State,
    _evaluate_user_pin,
)


def _pin(release_id=1, position="A2", monotonic_ts=1000.0, duration_seconds=180):
    return {
        "release_id": release_id,
        "track_position": position,
        "monotonic_ts": monotonic_ts,
        "duration_seconds": duration_seconds,
    }


# ---- pure helper -----------------------------------------------------


def test_pass_when_no_pin():
    action, streak, reason = _evaluate_user_pin(None, 0, 1, "A1", 1000.0)
    assert action == "pass"
    assert streak == 0
    assert reason == "no_pin"


def test_pin_survives_single_mis_shazam():
    action, streak, reason = _evaluate_user_pin(_pin(), 0, 1, "A1", 1001.0)
    assert action == "honor"
    assert streak == 1
    assert reason == "different_position"


def test_pin_released_after_three_consecutive_different_track():
    pin = _pin()
    action1, s1, _ = _evaluate_user_pin(pin, 0, 1, "A1", 1001.0)
    assert (action1, s1) == ("honor", 1)
    action2, s2, _ = _evaluate_user_pin(pin, s1, 1, "A1", 1002.0)
    assert (action2, s2) == ("honor", 2)
    action3, s3, reason3 = _evaluate_user_pin(pin, s2, 1, "A1", 1003.0)
    assert action3 == "clear"
    assert s3 == 0
    assert reason3 == "streak_exceeded"


def test_pin_streak_resets_on_pinned_position_confirm():
    pin = _pin(position="A2")
    _, s1, _ = _evaluate_user_pin(pin, 0, 1, "A1", 1001.0)
    action, s2, reason = _evaluate_user_pin(pin, s1, 1, "A2", 1002.0)
    assert action == "honor"
    assert s2 == 0
    assert reason == "same_position"
    _, s3, _ = _evaluate_user_pin(pin, s2, 1, "A1", 1003.0)
    _, s4, _ = _evaluate_user_pin(pin, s3, 1, "A1", 1004.0)
    assert s4 == 2  # never reaches 3 — pin still alive


def test_pin_released_on_different_release_id():
    action, streak, reason = _evaluate_user_pin(_pin(release_id=1), 0, 2, "A1", 1001.0)
    assert action == "clear"
    assert streak == 0
    assert reason == "different_release"


def test_pin_released_on_ttl_expiry():
    pin = _pin(monotonic_ts=1000.0, duration_seconds=120)
    later = 1000.0 + 120 + PIN_TTL_BUFFER_S + 1
    action, streak, reason = _evaluate_user_pin(pin, 0, 1, "A2", later)
    assert action == "clear"
    assert streak == 0
    assert reason == "ttl"


def test_pin_ttl_not_applied_when_duration_null():
    pin = _pin(monotonic_ts=1000.0, duration_seconds=None)
    action, _, reason = _evaluate_user_pin(pin, 0, 1, "A2", 10000.0)
    assert action == "honor"
    assert reason == "same_position"


def test_shazam_only_hit_honors_pin_without_advancing_streak():
    pin = _pin()
    action, streak, reason = _evaluate_user_pin(pin, 2, None, None, 1001.0)
    assert action == "honor"
    assert streak == 2  # untouched
    assert reason == "shazam_only"


def test_position_normalization_handles_case_and_whitespace():
    pin = _pin(position="A2")
    action, streak, reason = _evaluate_user_pin(pin, 0, 1, " a2 ", 1001.0)
    assert action == "honor"
    assert streak == 0
    assert reason == "same_position"


def test_ttl_takes_precedence_over_streak():
    pin = _pin(monotonic_ts=1000.0, duration_seconds=60)
    later = 1000.0 + 60 + PIN_TTL_BUFFER_S + 1
    action, streak, reason = _evaluate_user_pin(pin, 2, 1, "A1", later)
    assert action == "clear"
    assert reason == "ttl"
    assert streak == 0


def test_pin_expires_immediately_past_computed_ttl():
    """Acceptance criteria for pin-stays-active-past-ttl.

    Set a pin with duration=10s, advance clock 10.01s past pin time —
    pin must be cleared (TTL elapsed, no grace buffer). Previously the
    pin stayed active for up to PIN_TTL_BUFFER_S=15s past its TTL, which
    let promotion capture refs under the now-wrong label during track
    transitions (minor cohort poisoning).
    """
    pin = _pin(monotonic_ts=1000.0, duration_seconds=10)
    action, _, reason = _evaluate_user_pin(pin, 0, 1, "A1", 1010.01)
    assert action == "clear"
    assert reason == "ttl"
    # And it must still be honored a hair before the TTL.
    action_before, _, reason_before = _evaluate_user_pin(
        pin, 0, 1, "A1", 1009.99,
    )
    assert action_before == "honor"
    assert reason_before == "different_position"


def test_different_release_takes_precedence_over_same_position_normalization():
    pin = _pin(release_id=1, position="A2")
    action, _, reason = _evaluate_user_pin(pin, 0, 2, "A2", 1001.0)
    assert action == "clear"
    assert reason == "different_release"


# ---- State-level cleanup sites --------------------------------------


def test_state_initializes_pin_fields():
    s = State()
    assert s.user_track_pin is None
    assert s.pin_different_track_streak == 0


# ---- control.py cleanup sites (mark_wrong, next_track, select_release)


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from nowplaying import control


def _mk_request(state, body):
    app = {"state": state, "broadcaster": MagicMock(publish=AsyncMock())}
    req = MagicMock(spec=web.Request)
    req.app = app
    req.json = AsyncMock(return_value=body)
    return req


def test_mark_wrong_clears_pin():
    state = State()
    state.last_vinyl = {"title": "Stem"}
    state.user_track_pin = _pin()
    state.pin_different_track_streak = 2
    req = _mk_request(state, {"release_id": 1, "track_position": "A2"})
    asyncio.run(control.mark_wrong(req))
    assert state.user_track_pin is None
    assert state.pin_different_track_streak == 0


def test_select_release_clears_pin():
    state = State()
    state.last_vinyl = {"title": "Stem", "release_id": 1, "track_position": "A2"}
    state.user_track_pin = _pin(release_id=1)
    state.pin_different_track_streak = 1
    rel = {
        "title": "Other Album",
        "artist": "Other",
        "tracks": [
            {"position": "A1", "title": "Stem", "side": "A", "duration_seconds": 200},
        ],
    }
    with patch.object(control.discogs_catalog, "get_release", return_value=rel):
        req = _mk_request(state, {"release_id": 2})
        asyncio.run(control.select_release(req))
    assert state.user_track_pin is None
    assert state.pin_different_track_streak == 0


def test_next_track_advances_pin_when_active():
    """next_track advances pin to the new position with full-duration TTL.

    # Why: docs/features/pin-ttl-35s-phantom-elapsed/. The advanced track
    # is fresh-start (track_started_at_iso=None) so TTL = full duration.
    """
    state = State()
    tracklist = [
        {"position": "A1", "side": "A", "title": "Stem", "duration_seconds": 120},
        {"position": "A2", "side": "A", "title": "Long Stem", "duration_seconds": 400},
    ]
    state.last_vinyl = {
        "title": "Stem", "release_id": 1, "track_position": "A1",
        "tracklist": tracklist, "artist": "DJ Shadow", "album": "Endtroducing",
    }
    state.user_track_pin = _pin(release_id=1, position="A1", duration_seconds=120)
    state.pin_different_track_streak = 1
    req = _mk_request(state, {"release_id": 1, "current_track_position": "A1"})
    asyncio.run(control.next_track(req))
    assert state.user_track_pin is not None
    assert state.user_track_pin["track_position"] == "A2"
    assert state.user_track_pin["duration_seconds"] == 400
    assert state.user_track_pin["release_id"] == 1
    assert state.pin_different_track_streak == 0


def test_honor_path_patches_full_identity_fields():
    """Simulates the on_heartbeat honor-branch field copy directly.

    Mirrors the loop in main.on_heartbeat that overlays state.last_vinyl
    onto a fresh payload when _evaluate_user_pin returns 'honor'.
    """
    pinned_last_vinyl = {
        "release_id": 100,
        "track_position": "B1",
        "title": "Stem / Long Stem",
        "side": "B",
        "artist": "DJ Shadow",
        "album": "Endtroducing",
        "year": 1996,
        "label": "Mo Wax",
        "catno": "MW059",
        "duration_seconds": 540,
        "tracklist": [{"position": "B1", "side": "B", "title": "Stem / Long Stem"}],
        "track_started_at": "2026-05-14T12:00:00Z",
        "art_url": "/art/100",
    }
    shazam_payload = {
        "release_id": 999,
        "track_position": "A2",
        "title": "What Does Your Soul Look Like",
        "side": "A",
        "artist": "DJ Shadow",
        "album": "Endtroducing",
        "match_method": "shazam",
        "match_confidence": "high",
        "alternate_releases": [{"release_id": 888}],
        "art_url": "/art/999",
        "ts": "2026-05-14T12:00:15Z",
    }
    for fld in (
        "release_id", "track_position", "title", "side",
        "artist", "album", "year", "label", "catno",
        "duration_seconds", "tracklist", "track_started_at",
        "art_url",
    ):
        if fld in pinned_last_vinyl and pinned_last_vinyl.get(fld) is not None:
            shazam_payload[fld] = pinned_last_vinyl[fld]
    shazam_payload["match_method"] = "user-identified"
    shazam_payload["match_confidence"] = "user"
    shazam_payload.pop("alternate_releases", None)

    assert shazam_payload["release_id"] == 100
    assert shazam_payload["track_position"] == "B1"
    assert shazam_payload["title"] == "Stem / Long Stem"
    assert shazam_payload["art_url"] == "/art/100"
    assert shazam_payload["duration_seconds"] == 540
    assert shazam_payload["match_method"] == "user-identified"
    assert shazam_payload["match_confidence"] == "user"
    assert "alternate_releases" not in shazam_payload
    # Freshness fields preserved on the payload
    assert shazam_payload["ts"] == "2026-05-14T12:00:15Z"


def test_identify_clip_stores_duration_on_last_vinyl():
    """identify_clip stores full-duration TTL for fresh-start pins.

    # Why: docs/features/pin-ttl-35s-phantom-elapsed/. The identify flow
    # passes track_started_at_iso=None, so TTL is the full track duration.
    # last_vinyl["duration_seconds"] is the raw track duration for display.
    """
    state = State()
    rel = {
        "title": "Endtroducing", "artist": "DJ Shadow",
        "year": 1996, "label": "Mo Wax", "catno": "MW059",
        "tracks": [
            {"position": "B1", "side": "B", "title": "Stem / Long Stem",
             "duration_seconds": 540},
        ],
    }
    with patch.object(control.discogs_catalog, "get_release", return_value=rel), \
         patch.object(control, "_safe_art_fetch", new=AsyncMock()):
        req = _mk_request(state, {"release_id": 100, "track_position": "B1"})
        asyncio.run(control.identify_clip(req))
    assert state.last_vinyl is not None
    assert state.last_vinyl["duration_seconds"] == 540
    assert state.last_vinyl["side"] == "B"
    assert state.user_track_pin["duration_seconds"] == 540


def test_next_track_does_not_create_pin_when_none_active():
    state = State()
    tracklist = [
        {"position": "A1", "side": "A", "title": "Stem", "duration_seconds": 120},
        {"position": "A2", "side": "A", "title": "Long Stem", "duration_seconds": 400},
    ]
    state.last_vinyl = {
        "title": "Stem", "release_id": 1, "track_position": "A1",
        "tracklist": tracklist, "artist": "DJ Shadow", "album": "Endtroducing",
    }
    req = _mk_request(state, {"release_id": 1, "current_track_position": "A1"})
    asyncio.run(control.next_track(req))
    assert state.user_track_pin is None
