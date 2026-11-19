"""A predicted track holds once a quiet gap has advanced to it.

Scenarios are from a play of Failure, *Fantastic Planet* (release 31427573):
side A, where A1 was confirmed by Shazam and the gap before A2 advanced the
prediction, which was then pulled back to A1 within 12 seconds.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from nowplaying.orchestrator._class import Orchestrator
from nowplaying.orchestrator.state import State

SIDE_A = [
    {"position": "A1", "side": "A", "title": "Saturday Savior", "duration_seconds": 268},
    {"position": "A2", "side": "A", "title": "Sergeant Politeness", "duration_seconds": 246},
    {"position": "A3", "side": "A", "title": "Segue 1", "duration_seconds": 94},
    {"position": "A4", "side": "A", "title": "Smoking Umbrellas", "duration_seconds": 227},
]
RELEASE = {"id": 31427573, "tracks": SIDE_A}


def _iso_ago(seconds: float) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(seconds=seconds))
        .isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _state_a1_confirmed() -> State:
    """A1 confirmed by Shazam 270 s ago: its 268 s are up."""
    s = State()
    s.sonos_source = "vinyl"
    s.last_vinyl = {
        "source": "vinyl", "release_id": 31427573, "artist": "Failure",
        "album": "Fantastic Planet", "title": "Saturday Savior",
        "side": "A", "track_position": "A1", "duration_seconds": 268,
        "track_started_at": _iso_ago(270), "tracklist": SIDE_A,
        "match_method": "shazam",
    }
    return s


def _orch(state: State) -> Orchestrator:
    bcast = mock.AsyncMock()
    return Orchestrator(
        state=state, bcast=bcast, sonos_coord=None, stop=asyncio.Event(),
        llm=mock.AsyncMock(enabled=False), fingerprint_enabled=False,
    )


def _predict_a2(state: State, started_s_ago: float | None) -> None:
    state.predicted_position = {
        "release_id": 31427573, "release_mbid": None, "side": "A",
        "track_position": "A2", "index_in_side": 1, "duration_seconds": 246,
    }
    if started_s_ago is not None:
        state.predicted_position["track_started_at"] = _iso_ago(started_s_ago)


@pytest.mark.asyncio
async def test_guess_one_second_after_gap_advance_is_the_predicted_track():
    """16:02:51: `track-guess: window pos=A1 (est=1s)` right after
    `predicted: advanced to ... A2`. The side position is A2's start plus
    the time since the gap, not the time since the gap alone."""
    state = _state_a1_confirmed()
    _predict_a2(state, started_s_ago=1)
    loop_now = asyncio.get_running_loop().time()
    state.audible_up_at_mono = loop_now - 1
    orch = _orch(state)

    guess = orch._try_window_track_guess(
        state, 31427573, SIDE_A, lambda pos: pos, 1.0,
    )

    assert guess is not None
    assert guess["position"] == "A2"


@pytest.mark.asyncio
async def test_guess_never_moves_behind_the_predicted_track():
    """The backward guard compares with the track on screen (predicted A2),
    not the last confirmed one (A1)."""
    state = _state_a1_confirmed()
    _predict_a2(state, started_s_ago=None)
    orch = _orch(state)

    guess = orch._try_window_track_guess(
        state, 31427573, SIDE_A, lambda pos: pos, 1.0,
    )

    assert guess is not None
    assert guess["position"] == "A2"


@pytest.mark.asyncio
async def test_decay_judges_the_predicted_track_not_the_confirmed_one():
    """16:03:01: `state-decay: last_vinyl='Saturday Savior' past expected
    track end (stamp age=11.4s)` while A2 had been showing for 11 s."""
    state = _state_a1_confirmed()
    _predict_a2(state, started_s_ago=11)
    state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time() - 11.4
    orch = _orch(state)

    assert await orch._check_state_decay("vinyl", -20.0) is False


@pytest.mark.asyncio
async def test_decay_fires_when_the_predicted_track_has_run_its_length():
    state = _state_a1_confirmed()
    _predict_a2(state, started_s_ago=250)
    state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time() - 250
    orch = _orch(state)

    assert await orch._check_state_decay("vinyl", -20.0) is True


@pytest.mark.asyncio
async def test_gap_advance_records_the_predicted_tracks_start_and_length():
    """The audible edge after a gap advances A1 to A2 and stamps when A2
    started and how long it is, so the guess clock and decay can follow it."""
    state = _state_a1_confirmed()
    orch = _orch(state)
    orch.bcast.publish = mock.AsyncMock(return_value=True)

    with mock.patch(
        "nowplaying.catalog.get_release", return_value=RELEASE,
    ), mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()):
        await orch._handle_capture_audible()

    predicted = state.predicted_position
    assert predicted["track_position"] == "A2"
    assert predicted["duration_seconds"] == 246
    assert predicted["track_started_at"] is not None
