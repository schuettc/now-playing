"""The MBID-keyed fingerprint store is seeded from a Shazam confirmation,
the way the user pin seeds the Discogs store.

The seed places the clip at the Shazam match offset plus the time since
the confirm, and declines once that runs past the end of the confirmed
track: from there the audio belongs to the next track. These drive the
seed from the fields production sets on a Shazam-confirmed discovered
release (state.last_vinyl, state.last_shazam_match_unix_ts,
state.last_shazam_match_offset_s) with no hand-built anchor.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.orchestrator.streaming_idle import MUSIC_DB
from nowplaying.vinyl import promotion

_MONO_NOW = 2_000_000.0
_WALL_NOW = 1_787_000_000
_MUSIC_LEVEL = MUSIC_DB + 10.0
_FAKE_WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


def _discovered_last_vinyl(track_position="A1", duration=191):
    return {
        "release_id": None,
        "release_mbid": "mb-plans-1",
        "track_position": track_position,
        "tracklist": [
            {"position": "B1", "title": "Marching Bands", "duration_seconds": 252},
            {"position": track_position, "title": "Soul Meets Body",
             "duration_seconds": duration},
        ],
    }


@pytest.fixture
def orch(monkeypatch):
    from nowplaying.main import Orchestrator

    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = _MONO_NOW
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)
    monkeypatch.setattr(
        "nowplaying.orchestrator._heartbeat_handlers.time.time",
        lambda: _WALL_NOW,
    )

    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = True
    o.state = mock.MagicMock()
    o.state.user_track_pin = None
    o.state.fingerprint_anchor = None
    o.state.last_vinyl = None
    o.state.last_shazam_match_unix_ts = None
    o.state.last_shazam_match_offset_s = None
    o.state.last_shazam_match_position = "A1"
    o.state.sonos_source = "vinyl"
    return o


def _spy_promotion(monkeypatch) -> mock.AsyncMock:
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    promote = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(promotion, "maybe_promote", promote)
    return promote


async def _schedule(orch) -> None:
    with mock.patch("asyncio.create_task"):
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)


@pytest.mark.asyncio
async def test_seed_places_the_ref_at_offset_plus_time_since_confirm(
    orch, monkeypatch,
):
    orch.state.last_vinyl = _discovered_last_vinyl(duration=191)
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    orch.state.last_shazam_match_offset_s = 100.0
    promote = _spy_promotion(monkeypatch)
    await _schedule(orch)
    promote.assert_called_once()
    kw = promote.call_args.kwargs
    assert kw["release_id"] is None
    assert kw["mbid"] == "mb-plans-1"
    assert kw["track_position"] == "A1"
    assert abs(kw["track_position_s"] - 120) < 0.5
    assert kw["duration_s"] == 191


@pytest.mark.asyncio
async def test_seed_declines_once_past_the_end_of_the_confirmed_track(
    orch, monkeypatch,
):
    """Confirmed 180 s into a 191 s track, 20 s ago: the needle is on the
    next track now, so the clip must not be filed under this one."""
    orch.state.last_vinyl = _discovered_last_vinyl(duration=191)
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    orch.state.last_shazam_match_offset_s = 180.0
    promote = _spy_promotion(monkeypatch)
    await _schedule(orch)
    promote.assert_not_called()


@pytest.mark.asyncio
async def test_seed_declines_without_a_match_offset(orch, monkeypatch):
    orch.state.last_vinyl = _discovered_last_vinyl(duration=191)
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    promote = _spy_promotion(monkeypatch)
    await _schedule(orch)
    promote.assert_not_called()


@pytest.mark.asyncio
async def test_mbid_seed_not_used_when_anchor_present(orch, monkeypatch):
    """Precedence: a live MBID anchor wins over the seed (no double-write)."""
    orch.state.last_vinyl = _discovered_last_vinyl(track_position="A1")
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    orch.state.last_shazam_match_offset_s = 100.0
    orch.state.fingerprint_anchor = {
        "release_id": None, "mbid": "mb-plans-1", "track_position": "B2",
        "monotonic_ts": _MONO_NOW - 5, "duration_seconds": 200,
        "last_matched_ref_position_s": 30.0,
    }
    promote = _spy_promotion(monkeypatch)
    await _schedule(orch)
    promote.assert_called_once()
    assert promote.call_args.kwargs["track_position"] == "B2"


@pytest.mark.asyncio
async def test_discogs_lock_never_takes_mbid_seed(orch, monkeypatch):
    """A release with a Discogs release_id is owned by the pin/anchor
    Discogs path — the discovered seed must not hijack it, even if it also
    carries an mbid."""
    lv = _discovered_last_vinyl()
    lv["release_id"] = 3112846
    orch.state.last_vinyl = lv
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    orch.state.last_shazam_match_offset_s = 100.0
    promote = _spy_promotion(monkeypatch)
    await _schedule(orch)
    promote.assert_not_called()


@pytest.mark.asyncio
async def test_seed_declines_when_the_shown_track_is_not_the_confirmed_one(
    orch, monkeypatch,
):
    """Shazam confirmed A1; a fingerprint publish has since moved the
    display to A2. The offset belongs to A1, so it cannot place a clip
    in A2."""
    orch.state.last_vinyl = _discovered_last_vinyl(track_position="A2", duration=400)
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 200
    orch.state.last_shazam_match_offset_s = 30.0
    orch.state.last_shazam_match_position = "A1"
    promote = _spy_promotion(monkeypatch)
    await _schedule(orch)
    promote.assert_not_called()
