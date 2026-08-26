"""The MBID-keyed fingerprint store must be seeded from a Shazam
confirmation, the way the user pin seeds the Discogs store.

Without a seed the discovered cascade is a closed loop: an MBID anchor
can only be born from a fingerprint hit against the discovered store,
and writing to that store requires an MBID anchor. Live evidence: 113
discovered releases, 239 vinyl plays off-Discogs, 0 discovered fp_refs,
0 promotions ever scheduled.

These drive the seed from the fields production actually sets on a
Shazam-confirmed discovered release — state.last_vinyl (release_mbid,
track_position, tracklist, release_id=None) and
state.last_shazam_match_unix_ts — with NO hand-built anchor. That is the
reachability the old anchor-injecting test never had.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.discovery import fingerprint as discovery_fingerprint
from nowplaying.orchestrator.streaming_idle import MUSIC_DB

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
            {"position": "A1", "title": "Marching Bands", "duration_seconds": 252},
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
    o.state.sonos_source = "vinyl"
    return o


@pytest.mark.asyncio
async def test_mbid_seed_drive_fires_from_shazam_confirmed_discovered_release(
    orch, monkeypatch,
):
    """The red test: a recent Shazam confirm of a discovered release must
    schedule a discovered fp_ref, with no pin and no anchor."""
    orch.state.last_vinyl = _discovered_last_vinyl()
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    to_thread = mock.MagicMock(return_value=mock.MagicMock())
    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    with mock.patch("asyncio.create_task"):
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)
    to_thread.assert_called_once()
    args = to_thread.call_args.args
    assert args[0] is discovery_fingerprint.add_ref
    assert args[1] == "mb-plans-1"          # mbid
    assert args[2] == "A1"                   # track_position
    assert abs(args[3] - 20) < 0.5           # elapsed ≈ now - confirm ts


@pytest.mark.asyncio
async def test_mbid_seed_not_used_when_anchor_present(orch, monkeypatch):
    """Precedence: a live MBID anchor wins over the seed (no double-write)."""
    orch.state.last_vinyl = _discovered_last_vinyl(track_position="A1")
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    orch.state.fingerprint_anchor = {
        "release_id": None, "mbid": "mb-plans-1", "track_position": "B2",
        "monotonic_ts": _MONO_NOW - 5, "duration_seconds": 200,
        "last_matched_ref_position_s": 30.0,
    }
    to_thread = mock.MagicMock(return_value=mock.MagicMock())
    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    with mock.patch("asyncio.create_task"):
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)
    to_thread.assert_called_once()
    assert to_thread.call_args.args[2] == "B2"   # anchor's position, not seed's


@pytest.mark.asyncio
async def test_mbid_seed_ignored_when_shazam_stamp_stale(orch, monkeypatch):
    """A confirm older than the track duration would tag the ref at a
    wildly wrong position — decline rather than poison the store."""
    orch.state.last_vinyl = _discovered_last_vinyl(duration=191)
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 500
    to_thread = mock.MagicMock(return_value=mock.MagicMock())
    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    add_ref = mock.MagicMock()
    monkeypatch.setattr(discovery_fingerprint, "add_ref", add_ref)
    with mock.patch("asyncio.create_task") as create_task:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)
    to_thread.assert_not_called()
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_discogs_lock_never_takes_mbid_seed(orch, monkeypatch):
    """A release with a Discogs release_id is owned by the pin/anchor
    Discogs path — the discovered seed must not hijack it, even if it also
    carries an mbid."""
    lv = _discovered_last_vinyl()
    lv["release_id"] = 3112846
    orch.state.last_vinyl = lv
    orch.state.last_shazam_match_unix_ts = _WALL_NOW - 20
    to_thread = mock.MagicMock(return_value=mock.MagicMock())
    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    with mock.patch("asyncio.create_task"):
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)
    to_thread.assert_not_called()      # seed declined; no discovered add_ref
