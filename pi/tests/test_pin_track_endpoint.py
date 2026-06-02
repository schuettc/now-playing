"""Tests for POST /api/pin-track — the locked-album fast-path pin endpoint.

Covers the contract specified in
docs/features/tracklist-click-to-identify/plan.md:
  - happy path with canonical pinned payload
  - in-place state.last_vinyl mutation
  - broadcaster publish
  - 4xx machine-readable `reason` codes
  - case/whitespace tolerance on track_position
  - pin_ttl_seconds null when duration unknown
  - pin TTL computed from remaining time (pin-ttl-remaining-time)
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying import control  # noqa: E402
from nowplaying.main import MIN_PIN_TTL_S, State  # noqa: E402
from nowplaying.orchestrator.pin import ASSUMED_LOCK_POSITION_S  # noqa: E402


def _mk_request(state, body):
    app = {"state": state, "broadcaster": MagicMock(publish=AsyncMock())}
    req = MagicMock(spec=web.Request)
    req.app = app
    req.json = AsyncMock(return_value=body)
    return req


def _locked_album(release_id=100):
    tracklist = [
        {"position": "A1", "side": "A", "title": "Endtroducing",
         "duration_seconds": 200},
        {"position": "B3", "side": "B", "title": "Stem / Long Stem",
         "duration_seconds": 540},
        {"position": "C2", "side": "C", "title": "Mutual Slump",
         "duration_seconds": None},
    ]
    return {
        "release_id": release_id,
        "track_position": "A1",
        "title": "Endtroducing",
        "side": "A",
        "artist": "DJ Shadow",
        "album": "Endtroducing",
        "year": 1996,
        "label": "Mo Wax",
        "catno": "MW059",
        "duration_seconds": 200,
        "tracklist": tracklist,
        "track_started_at": "2026-05-16T12:00:00Z",
        "art_url": "/art/100",
    }


def _run(coro):
    return asyncio.run(coro)


# ---- happy path -----------------------------------------------------


def test_pin_track_happy_path_returns_canonical_payload():
    """pin_ttl_seconds is the scaling-lock hold: with no reliable position cue
    the lock assumes ASSUMED_LOCK_POSITION_S into the track, so the hold is
    duration − ASSUMED_LOCK_POSITION_S. See
    docs/features/advance-on-shazam-quiet-records/.
    """
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    resp = _run(control.pin_track(req))
    assert resp.status == 200
    import json
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is True
    assert payload["release_id"] == 100
    assert payload["track_position"] == "B3"
    assert payload["title"] == "Stem / Long Stem"
    assert payload["duration_seconds"] == 540
    assert payload["pin_ttl_seconds"] == 540 - ASSUMED_LOCK_POSITION_S


def test_pin_track_sets_user_track_pin():
    """Pin duration stored in state is the scaling-lock hold (= full duration
    minus the assumed lock position when no reliable cue exists)."""
    state = State()
    state.last_vinyl = _locked_album()
    state.pin_different_track_streak = 2
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    _run(control.pin_track(req))
    assert state.user_track_pin is not None
    assert state.user_track_pin["release_id"] == 100
    assert state.user_track_pin["track_position"] == "B3"
    assert state.user_track_pin["duration_seconds"] == 540 - ASSUMED_LOCK_POSITION_S
    assert state.pin_different_track_streak == 0


def test_pin_track_mutates_last_vinyl_in_place():
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    _run(control.pin_track(req))
    lv = state.last_vinyl
    assert lv["track_position"] == "B3"
    assert lv["title"] == "Stem / Long Stem"
    assert lv["duration_seconds"] == 540
    assert lv["side"] == "B"
    assert lv["match_method"] == "user-identified"
    # release_id and other identity fields preserved
    assert lv["release_id"] == 100
    assert lv["artist"] == "DJ Shadow"


def test_pin_track_publishes_to_broadcaster():
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    bcast = req.app["broadcaster"]
    _run(control.pin_track(req))
    bcast.publish.assert_awaited_once()
    published = bcast.publish.await_args[0][0]
    assert published["track_position"] == "B3"
    assert published["title"] == "Stem / Long Stem"


def test_pin_track_position_normalizes_case_and_whitespace():
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": " b3 "})
    resp = _run(control.pin_track(req))
    assert resp.status == 200
    # Response normalizes to canonical casing from tracklist
    import json
    payload = json.loads(resp.body.decode())
    assert payload["track_position"] == "B3"
    assert state.user_track_pin["track_position"] == "B3"


def test_pin_track_pin_ttl_null_when_duration_unknown():
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": "C2"})
    resp = _run(control.pin_track(req))
    import json
    payload = json.loads(resp.body.decode())
    assert payload["duration_seconds"] is None
    assert payload["pin_ttl_seconds"] is None
    # And last_vinyl drops the stale duration when the new track has none
    assert "duration_seconds" not in state.last_vinyl


# ---- 4xx error paths ------------------------------------------------


def test_pin_track_no_album_locked_when_last_vinyl_none():
    state = State()
    state.last_vinyl = None
    req = _mk_request(state, {"release_id": 100, "track_position": "A1"})
    resp = _run(control.pin_track(req))
    assert resp.status == 400
    import json
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is False
    assert payload["reason"] == "no-album-locked"
    # No state mutation
    assert state.user_track_pin is None


def test_pin_track_no_album_locked_when_release_id_missing():
    state = State()
    state.last_vinyl = {"title": "Stem"}  # no release_id key
    req = _mk_request(state, {"release_id": 100, "track_position": "A1"})
    resp = _run(control.pin_track(req))
    assert resp.status == 400
    import json
    payload = json.loads(resp.body.decode())
    assert payload["reason"] == "no-album-locked"


def test_pin_track_release_id_mismatch():
    state = State()
    state.last_vinyl = _locked_album(release_id=100)
    req = _mk_request(state, {"release_id": 200, "track_position": "B3"})
    resp = _run(control.pin_track(req))
    assert resp.status == 400
    import json
    payload = json.loads(resp.body.decode())
    assert payload["reason"] == "release-id-mismatch"
    assert state.user_track_pin is None
    # last_vinyl untouched
    assert state.last_vinyl["track_position"] == "A1"


def test_pin_track_position_not_in_tracklist():
    # Z9 is not in the inline tracklist and also not in the catalog
    # (catalog returns None) → 400 position-not-in-tracklist.
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": "Z9"})
    with patch("nowplaying.control.pin_track.discogs_catalog.get_release", return_value=None):
        resp = _run(control.pin_track(req))
    assert resp.status == 400
    import json
    payload = json.loads(resp.body.decode())
    assert payload["reason"] == "position-not-in-tracklist"
    assert state.user_track_pin is None
    # last_vinyl untouched on failure
    assert state.last_vinyl["track_position"] == "A1"


def test_pin_track_bad_request_missing_field():
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100})  # missing track_position
    resp = _run(control.pin_track(req))
    assert resp.status == 400
    import json
    payload = json.loads(resp.body.decode())
    assert payload["reason"] == "bad-request"


def test_pin_track_bad_request_wrong_type():
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": "not-an-int", "track_position": "A1"})
    resp = _run(control.pin_track(req))
    assert resp.status == 400
    import json
    payload = json.loads(resp.body.decode())
    assert payload["reason"] == "bad-request"


# ---- fallback / 503 -------------------------------------------------


def test_pin_track_503_when_orchestrator_not_ready():
    req = MagicMock(spec=web.Request)
    req.app = {"state": None, "broadcaster": None}
    resp = _run(control.pin_track(req))
    assert resp.status == 503


def test_pin_track_falls_back_to_catalog_when_payload_tracklist_empty():
    """Sparse Shazam-only payload that locked a release without
    hydrating the tracklist should still allow pinning by falling back
    to the local Discogs catalog (same path next_track uses).
    """
    state = State()
    state.last_vinyl = {
        "release_id": 100,
        "track_position": "A1",
        "artist": "DJ Shadow",
        "album": "Endtroducing",
        # no tracklist
    }
    rel = {
        "title": "Endtroducing", "artist": "DJ Shadow",
        "year": 1996, "label": "Mo Wax", "catno": "MW059",
        "tracks": [
            {"position": "B3", "side": "B", "title": "Stem / Long Stem",
             "duration_seconds": 540},
        ],
    }
    with patch.object(control.discogs_catalog, "get_release", return_value=rel):
        req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
        resp = _run(control.pin_track(req))
    assert resp.status == 200
    assert state.user_track_pin["track_position"] == "B3"
    # No reliable cue → scaling-lock hold = duration − assumed lock position.
    assert state.user_track_pin["duration_seconds"] == 540 - ASSUMED_LOCK_POSITION_S


# ---- scaling-lock hold tests (advance-on-shazam-quiet-records) -------


def test_pin_ttl_ignores_track_started_at_drift():
    """A manual lock must NOT be shortened by the dead-reckoned
    state.track_started_at — only reliable position cues scale the hold.

    Scenario: 180s track, state.track_started_at says 30s ago (drift-prone).
    With no reliable cue (no anchor / audible-edge / first-miss), the lock
    assumes ASSUMED_LOCK_POSITION_S, so the hold is 180 − 45 = 135 —
    independent of track_started_at.
    """
    state = State()
    state.track_started_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30),
    )
    tracklist = [{"position": "A1", "side": "A", "title": "Turntabled", "duration_seconds": 180}]
    state.last_vinyl = {
        "release_id": 42,
        "track_position": "A1",
        "artist": "Test Artist",
        "album": "Test Album",
        "duration_seconds": 180,
        "tracklist": tracklist,
    }
    req = _mk_request(state, {"release_id": 42, "track_position": "A1"})
    resp = _run(control.pin_track(req))

    import json
    payload = json.loads(resp.body.decode())
    assert resp.status == 200
    expected_ttl = 180 - ASSUMED_LOCK_POSITION_S
    assert payload["pin_ttl_seconds"] == expected_ttl
    assert state.user_track_pin["duration_seconds"] == expected_ttl
    # Raw duration still reported in response body for display
    assert payload["duration_seconds"] == 180


def test_pin_ttl_assumes_lock_position_when_no_cue():
    """With no reliable position cue, the hold = duration − assumed position."""
    state = State()
    state.last_vinyl = {
        "release_id": 42,
        "track_position": "A1",
        "artist": "Test Artist",
        "album": "Test Album",
        "tracklist": [
            {"position": "A1", "side": "A", "title": "Song", "duration_seconds": 200},
        ],
    }
    req = _mk_request(state, {"release_id": 42, "track_position": "A1"})
    resp = _run(control.pin_track(req))
    import json
    payload = json.loads(resp.body.decode())
    assert payload["pin_ttl_seconds"] == 200 - ASSUMED_LOCK_POSITION_S


def test_pin_ttl_assumed_position_capped_by_short_track_duration():
    """Locking a short track must not assume it's almost over.

    Regression for the live 'Lucky' case (48s track): a flat 45s assumption
    made the hold 48-45=3 → floored to 30 and born entirely in the decay
    window, so predicted-advance jumped immediately. The assumption is capped
    at duration/3, so a 48s track assumes ~16s in → hold ≈ 32 (NOT floored).
    See docs/features/advance-on-shazam-quiet-records/.
    """
    state = State()
    state.last_vinyl = {
        "release_id": 42,
        "track_position": "A3",
        "artist": "Local H",
        "album": "Pack Up The Cats",
        "tracklist": [
            {"position": "A3", "side": "A", "title": "Lucky", "duration_seconds": 48},
        ],
    }
    req = _mk_request(state, {"release_id": 42, "track_position": "A3"})
    resp = _run(control.pin_track(req))
    import json
    payload = json.loads(resp.body.decode())
    # lock_position = min(45, 48/3=16) = 16 → hold = 48 - 16 = 32 (above floor)
    assert payload["pin_ttl_seconds"] == 32
    assert state.user_track_pin["duration_seconds"] == 32


def test_pin_ttl_floored_at_min_pin_ttl_near_end_of_track():
    """A pin whose reliable cue lands near the track end is floored at
    MIN_PIN_TTL_S.

    Scenario: 90s track, Shazam first-miss 70s ago (a reliable position cue).
    hold = 90 − 70 = 20 → floored to MIN_PIN_TTL_S (30s).
    """
    now_epoch = 1_600_000_000.0  # fixed epoch — no sub-second rounding
    first_miss_at = int(now_epoch - 70)

    state = State()
    state.last_unmatched_after_match_unix_ts = first_miss_at
    state.last_vinyl = {
        "release_id": 42,
        "track_position": "A1",
        "artist": "Test",
        "album": "Short",
        "tracklist": [
            {"position": "A1", "side": "A", "title": "Brief", "duration_seconds": 90},
        ],
    }
    req = _mk_request(state, {"release_id": 42, "track_position": "A1"})

    with patch("nowplaying.control._shared.time.time", return_value=now_epoch):
        resp = _run(control.pin_track(req))

    import json
    payload = json.loads(resp.body.decode())
    # 90 - 70 = 20 → floored to 30
    assert payload["pin_ttl_seconds"] == MIN_PIN_TTL_S
    assert state.user_track_pin["duration_seconds"] == MIN_PIN_TTL_S


def test_pin_ttl_none_when_duration_unknown():
    """When duration is unknown, pin_ttl_seconds is null (existing behavior preserved)."""
    state = State()
    state.last_vinyl = _locked_album()
    req = _mk_request(state, {"release_id": 100, "track_position": "C2"})
    resp = _run(control.pin_track(req))
    import json
    payload = json.loads(resp.body.decode())
    assert payload["duration_seconds"] is None
    assert payload["pin_ttl_seconds"] is None
    assert state.user_track_pin["duration_seconds"] is None


# ---- predicted-advance latency (forward refs anchor) ----------------


def test_pin_after_predicted_advance_sets_initial_position_from_first_miss():
    """When pin lands after predicted-advance, initial_track_position_s
    reflects elapsed since the first Shazam miss after the prior confirm
    — NOT 0.0. See docs/features/pin-position-ignores-predicted-advance-latency/.

    Scenario: Shazam confirmed prior track, then 2 misses fired
    predicted-advance, then user pinned. ~30s of real audio has played.
    """
    now_epoch = 1_700_000_000.0
    first_miss_at = int(now_epoch - 30)  # first miss was 30s ago

    state = State()
    state.last_vinyl = _locked_album()
    state.last_unmatched_after_match_unix_ts = first_miss_at
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    with patch("nowplaying.control._shared.time.time", return_value=now_epoch):
        resp = _run(control.pin_track(req))
    assert resp.status == 200
    assert state.user_track_pin is not None
    initial_pos = state.user_track_pin["initial_track_position_s"]
    assert 28.0 <= initial_pos <= 32.0, (
        f"expected ~30s initial position, got {initial_pos}"
    )
    # track_started_at should reflect the inferred track start, not pin time
    expected_started_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(first_miss_at),
    )
    assert state.track_started_at == expected_started_iso


def test_pin_after_predicted_advance_ttl_subtracts_initial_position():
    """When pin lands after predicted-advance, the Shazam first-miss is a
    reliable position cue, so the hold scales from it — the pin doesn't
    outlive the real track end.

    See docs/features/advance-on-shazam-quiet-records/.

    Scenario: 540s track (B3), first miss 30s ago. hold = 540 − 30 = 510
    (no safety margin — the decay window replaces it).
    """
    now_epoch = 1_700_000_000.0
    first_miss_at = int(now_epoch - 30)

    state = State()
    state.last_vinyl = _locked_album()
    state.last_unmatched_after_match_unix_ts = first_miss_at
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    with patch("nowplaying.control._shared.time.time", return_value=now_epoch):
        resp = _run(control.pin_track(req))

    import json
    payload = json.loads(resp.body.decode())
    assert resp.status == 200
    # 540 - 30 (reliable first-miss elapsed) = 510
    expected_ttl = max(MIN_PIN_TTL_S, 540 - 30)
    assert payload["pin_ttl_seconds"] == expected_ttl, (
        f"expected TTL ~{expected_ttl} (= 540 - 30); got "
        f"{payload['pin_ttl_seconds']}"
    )
    assert state.user_track_pin["duration_seconds"] == expected_ttl


def test_pin_without_first_miss_history_uses_zero_initial_position():
    """Regression: pin with no prior recognition history → initial=0.0 (unchanged).
    """
    state = State()
    state.last_vinyl = _locked_album()
    state.last_unmatched_after_match_unix_ts = None
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    resp = _run(control.pin_track(req))
    assert resp.status == 200
    assert state.user_track_pin["initial_track_position_s"] == 0.0


# ---- backfill boundary integration (pin-backfill-boundary-clobbered-by-self) -


def test_pin_backfill_uses_prior_shazam_when_no_prior_pin():
    """First pin in session: state.last_pin_unix_ts is None at handler entry.

    The handler must pass the *prior* (None) value through to the backfill
    scheduler so the boundary anchors to last_shazam_match_unix_ts, NOT to
    the current pin's just-stamped timestamp.

    Regression guard for the bug where `_apply_user_track_pin` clobbered
    `state.last_pin_unix_ts` BEFORE `_schedule_pin_backfill` read it,
    collapsing the window to [pin_ts, pin_ts] and scheduling zero clips.

    See docs/features/pin-backfill-boundary-clobbered-by-self/.
    """
    now_epoch = 1_700_000_000.0
    shazam_ts = int(now_epoch - 30)

    state = State()
    state.last_vinyl = _locked_album()
    state.last_shazam_match_unix_ts = shazam_ts
    state.last_pin_unix_ts = None
    # B3 differs from the locked-album's A1, so the predicted-transition
    # boundary path is exercised.
    async def _drive():
        req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
        with patch(
            "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
            new=AsyncMock(return_value=1),
        ) as mock_backfill, patch(
            "nowplaying.control.pin_track.time.time", return_value=now_epoch,
        ):
            resp = await control.pin_track(req)
            await asyncio.sleep(0)  # let the create_task'd backfill awaitable run
        return resp, mock_backfill

    resp, mock_backfill = _run(_drive())
    assert resp.status == 200
    assert mock_backfill.called, "backfill must be scheduled, not skipped"
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == shazam_ts, (
        f"boundary must anchor to prior Shazam ts {shazam_ts}, "
        f"got {kwargs['audible_edge_unix_ts']} (likely pin's own ts)"
    )
    assert kwargs["pin_unix_ts"] == int(now_epoch)


def test_pin_backfill_uses_prior_pin_when_more_recent_than_shazam():
    """Chained pin: a prior pin exists from a few minutes ago, Shazam is older.

    Handler must pass the *prior* last_pin_unix_ts (not the value it
    just stamped via _apply_user_track_pin) to the backfill scheduler.
    """
    now_epoch = 1_700_000_000.0
    shazam_ts = int(now_epoch - 300)  # 5 min ago
    prior_pin_ts = int(now_epoch - 60)  # 1 min ago — more recent

    state = State()
    state.last_vinyl = _locked_album()
    state.last_shazam_match_unix_ts = shazam_ts
    state.last_pin_unix_ts = prior_pin_ts
    async def _drive():
        req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
        with patch(
            "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
            new=AsyncMock(return_value=1),
        ) as mock_backfill, patch(
            "nowplaying.control.pin_track.time.time", return_value=now_epoch,
        ):
            resp = await control.pin_track(req)
            await asyncio.sleep(0)
        return resp, mock_backfill

    resp, mock_backfill = _run(_drive())
    assert resp.status == 200
    assert mock_backfill.called
    kwargs = mock_backfill.call_args.kwargs
    assert kwargs["audible_edge_unix_ts"] == prior_pin_ts, (
        f"boundary must anchor to PRIOR pin ts {prior_pin_ts}, "
        f"got {kwargs['audible_edge_unix_ts']} (likely current pin's ts)"
    )
    # And state must STILL be updated to the new pin's ts for the next pin.
    assert state.last_pin_unix_ts >= prior_pin_ts
    assert state.last_pin_unix_ts == int(now_epoch)


def test_pin_with_stale_first_miss_ts_falls_back_to_zero():
    """If the first-miss timestamp is older than the max-age window, do not
    use it — fall back to 0.0. Prevents stale state from poisoning a fresh
    needle drop hours later."""
    now_epoch = 1_700_000_000.0
    state = State()
    state.last_vinyl = _locked_album()
    # 10 minutes ago — beyond the 300s max-age window
    state.last_unmatched_after_match_unix_ts = int(now_epoch - 600)
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    with patch("nowplaying.control._shared.time.time", return_value=now_epoch):
        resp = _run(control.pin_track(req))
    assert resp.status == 200
    assert state.user_track_pin["initial_track_position_s"] == 0.0
