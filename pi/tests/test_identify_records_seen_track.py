"""A user identify/pin must count as a recognition for session memory.

On a Shazam-quiet record the user identifies a track manually instead of
Shazam confirming it. If that identify does not record the track position
into `state.tracks_seen_since_audible_edge`, the fresh-side-first-track
gate sees an empty set when the NEXT track is later pinned and wrongly
classifies track 2+ as the first track of the side — sweeping up the
prior track's audio under the wrong label (cohort poisoning).

See docs/features/advance-on-shazam-quiet-records/.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying import control  # noqa: E402
from nowplaying.control._shared import (  # noqa: E402
    _apply_user_track_pin,
    _is_fresh_side_first_track_for_pin,
    _no_other_track_seen,
)
from nowplaying.orchestrator.state import State  # noqa: E402


def _audible_edge(ts_unix: int) -> dict:
    return {
        "type": "audible",
        "ts_iso": datetime.fromtimestamp(ts_unix, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
        "_ts_mono": 0.0,
    }


def _mk_request(state, body):
    app = {"state": state, "broadcaster": MagicMock(publish=AsyncMock())}
    req = MagicMock()
    req.app = app
    req.json = AsyncMock(return_value=body)
    return req


# ── unit: _apply_user_track_pin records the position ──────────────────


def test_apply_user_track_pin_records_seen_track():
    """Pinning A1 must add 'A1' to the session-seen set, just like a
    Shazam confirm would."""
    async def _run():
        state = State()
        _apply_user_track_pin(state, 100, "A1", {"duration_seconds": 200})
        return state

    state = asyncio.run(_run())
    assert "A1" in state.tracks_seen_since_audible_edge


def test_second_track_not_first_track_after_identify():
    """After A1 is identified, a later pin on A2 must NOT be treated as
    the first track of the side — the gate and `_no_other_track_seen`
    must both reject it."""
    async def _run():
        state = State()
        state.recent_audible_edges = [_audible_edge(int(time.time()) - 60)]
        # User identifies A1 (Shazam-quiet record).
        _apply_user_track_pin(state, 100, "A1", {"duration_seconds": 200})
        # Later the needle reaches A2; a pin is set there. By gate-check
        # time `_apply_pin_to_locked` has overwritten last_vinyl to A2.
        state.last_vinyl = {"release_id": 100, "track_position": "A2"}
        return state

    state = asyncio.run(_run())
    assert _no_other_track_seen(state, "A2") is False
    assert _is_fresh_side_first_track_for_pin(state, 100, "A2") is False


def test_first_track_still_first_track_after_identify():
    """A1 itself is unaffected: identifying A1 doesn't make A1 count as
    'another' track relative to itself, so the first-track gate still
    holds for A1."""
    async def _run():
        state = State()
        state.recent_audible_edges = [_audible_edge(int(time.time()) - 10)]
        _apply_user_track_pin(state, 100, "A1", {"duration_seconds": 200})
        state.last_vinyl = {"release_id": 100, "track_position": "A1"}
        return state

    state = asyncio.run(_run())
    assert _no_other_track_seen(state, "A1") is True
    assert _is_fresh_side_first_track_for_pin(state, 100, "A1") is True


# ── integration: both endpoint paths populate the set ─────────────────


def test_identify_clip_records_seen_track():
    """The full identify_clip endpoint records the pinned position."""
    state = State()
    rel = {
        "title": "Pack Up the Cats", "artist": "Local H",
        "tracks": [
            {"position": "A1", "side": "A", "title": "Cha!",
             "duration_seconds": 30},
        ],
    }
    with patch.object(control.discogs_catalog, "get_release", return_value=rel), \
         patch.object(control, "_safe_art_fetch", new=AsyncMock()):
        req = _mk_request(state, {"release_id": 100, "track_position": "A1"})
        asyncio.run(control.identify_clip(req))
    assert "A1" in state.tracks_seen_since_audible_edge


def test_pin_track_records_seen_track():
    """The full pin_track endpoint records the canonical pinned position."""
    from nowplaying.control.pin_track import pin_track

    state = State()
    state.last_vinyl = {
        "release_id": 100, "track_position": "A1",
        "tracklist": [
            {"position": "A1", "side": "A", "title": "Cha!",
             "duration_seconds": 30},
            {"position": "A2", "side": "A", "title": "All the Kids Are Right",
             "duration_seconds": 200},
        ],
        "artist": "Local H", "album": "Pack Up the Cats",
    }
    with patch(
        "nowplaying.control.pin_track.promotion.schedule_backfill_promotions",
        new=AsyncMock(return_value=0),
    ), patch.object(control, "_safe_art_fetch", new=AsyncMock()):
        req = _mk_request(state, {"release_id": 100, "track_position": "A2"})
        asyncio.run(pin_track(req))
    assert "A2" in state.tracks_seen_since_audible_edge
