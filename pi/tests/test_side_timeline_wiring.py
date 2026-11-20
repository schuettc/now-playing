"""The orchestrator follows the side timeline: sound returning, a track's
time running out and a match all go through `state.side_timeline`.

Scenarios are from the 2026-11-20 play of Failure, *Fantastic Planet*
(release 31427573), with the Pi's Discogs lengths.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from nowplaying.orchestrator import side_timeline
from nowplaying.orchestrator._class import Orchestrator
from nowplaying.orchestrator.state import State

RID = 31427573
_TRACKS = [
    ("A1", "Saturday Savior", 268), ("A2", "Sergeant Politeness", 245),
    ("A3", "Segue 1", 114), ("A4", "Smoking Umbrellas", 239),
    ("B5", "Pillowhead", 130), ("B6", "Blank", 339), ("B7", "Segue 2", 77),
    ("B8", "Dirty Blue Balloons", 267), ("B9", "Solaris", 223),
    ("C10", "Pitiful", 286), ("C11", "Leo", 186), ("C12", "Segue 3", 132),
    ("C13", "The Nurse Who Loved Me", 266), ("C14", "Another Space Song", 313),
    ("D15", "Stuck on You", 269), ("D16", "Heliotropic", 375),
    ("D17", "Daylight", 362),
]
TRACKS = [
    {"position": p, "side": p[0], "title": t, "duration_seconds": d}
    for p, t, d in _TRACKS
]
RELEASE = {"id": RID, "tracks": TRACKS}
BY_POS = {t["position"]: t for t in TRACKS}


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _following(position: str, started_s_ago: float) -> State:
    """The side's first track was confirmed by Shazam; `position` is on
    screen as a prediction and current on the timeline."""
    track = BY_POS[position]
    first = next(t for t in TRACKS if t["side"] == track["side"])
    started = _ago(started_s_ago)
    s = State()
    s.sonos_source = "vinyl"
    s.last_vinyl = {
        "source": "vinyl", "release_id": RID, "artist": "Failure",
        "album": "Fantastic Planet", "title": first["title"],
        "side": first["side"], "track_position": first["position"],
        "duration_seconds": first["duration_seconds"],
        "track_started_at": _iso(_ago(started_s_ago + 600)),
        "tracklist": TRACKS, "match_method": "shazam",
    }
    s.last_vinyl_confidence_set_at = asyncio.get_running_loop().time() - started_s_ago
    s.predicted_position = {
        "release_id": RID, "release_mbid": None, "side": track["side"],
        "track_position": position, "duration_seconds": track["duration_seconds"],
        "track_started_at": _iso(started),
    }
    s.track_started_at = _iso(started)
    s.side_timeline = side_timeline.start_at(TRACKS, position, started, RID, None)
    return s


def _orch(state: State) -> Orchestrator:
    bcast = mock.AsyncMock()
    bcast.publish = mock.AsyncMock(return_value=True)
    return Orchestrator(
        state=state, bcast=bcast, sonos_coord=None, stop=asyncio.Event(),
        llm=mock.AsyncMock(enabled=False), fingerprint_enabled=False,
    )


def _catalog():
    return mock.patch("nowplaying.catalog.get_release", return_value=RELEASE)


def _history():
    return mock.patch("nowplaying.history.record_play", new=mock.AsyncMock())


def _published(orch: Orchestrator) -> list[dict]:
    return [c.args[0] for c in orch.bcast.publish.call_args_list]


async def _quiet_then_sound(orch: Orchestrator, quiet_s: float) -> None:
    orch._handle_capture_silent()
    orch.state.quiet_began_at = _ago(quiet_s)
    await orch._handle_capture_audible()


@pytest.mark.asyncio
async def test_flip_from_the_last_track_shows_the_next_sides_first_track():
    state = _following("B9", started_s_ago=210)
    orch = _orch(state)

    with _catalog(), _history():
        await _quiet_then_sound(orch, quiet_s=33)

    assert state.predicted_position["track_position"] == "C10"
    assert state.side_timeline.side == "C"
    published = _published(orch)
    assert len(published) == 1
    assert published[0]["title"] == "Pitiful"
    assert state.quiet_began_at is None


@pytest.mark.asyncio
async def test_run_out_starts_the_next_track_at_the_listed_end():
    state = _following("A2", started_s_ago=250)
    a2_started = state.side_timeline.started_at
    orch = _orch(state)

    with _catalog(), _history():
        await orch._handle_unmatched_heartbeat("vinyl", -20.0)

    published = _published(orch)
    assert [p["track_position"] for p in published] == ["A3"]
    listed_end = a2_started + timedelta(seconds=245)
    started = _parse(published[0]["track_started_at"])
    assert abs((started - listed_end).total_seconds()) <= 1
    assert abs((started - _ago(5)).total_seconds()) <= 2
    assert state.predicted_position["track_started_at"] == published[0]["track_started_at"]


@pytest.mark.asyncio
async def test_gap_after_a_run_out_does_not_advance_twice():
    state = _following("B8", started_s_ago=270)
    orch = _orch(state)

    with _catalog(), _history():
        await orch._handle_unmatched_heartbeat("vinyl", -20.0)
        assert state.predicted_position["track_position"] == "B9"
        await _quiet_then_sound(orch, quiet_s=10)

    assert state.predicted_position["track_position"] == "B9"
    assert state.side_timeline.position == "B9"
    started = _parse(state.predicted_position["track_started_at"])
    assert abs((started - _ago(0)).total_seconds()) <= 2
    assert _published(orch)[-1]["track_started_at"] == state.predicted_position["track_started_at"]
    assert state.track_started_at == state.predicted_position["track_started_at"]


@pytest.mark.asyncio
async def test_gap_far_from_a_boundary_keeps_the_track():
    state = _following("C13", started_s_ago=173)
    started = state.predicted_position["track_started_at"]
    orch = _orch(state)

    with _catalog(), _history() as record_play:
        await _quiet_then_sound(orch, quiet_s=13)

    assert state.predicted_position["track_position"] == "C13"
    assert state.predicted_position["track_started_at"] == started
    assert state.track_started_at == started
    record_play.assert_not_called()
    orch.bcast.publish.assert_not_called()
    assert state.idle_task is None


@pytest.mark.asyncio
async def test_last_track_is_not_decayed_when_its_time_is_up():
    state = _following("B9", started_s_ago=235)
    orch = _orch(state)

    with _catalog(), _history():
        for _ in range(4):
            await orch._handle_unmatched_heartbeat("vinyl", -20.0)

    assert state.predicted_position["track_position"] == "B9"
    assert all(p.get("state") != "NEEDS_ID" for p in _published(orch))
    assert all(p.get("track_position") == "B9" for p in _published(orch))


@pytest.mark.asyncio
async def test_no_run_out_advance_while_quiet():
    state = _following("C12", started_s_ago=140)
    state.quiet_began_at = _ago(57)
    orch = _orch(state)

    with _catalog(), _history():
        await orch._handle_unmatched_heartbeat("vinyl", -40.0)

    assert state.side_timeline.position == "C12"
    orch.bcast.publish.assert_not_called()


@pytest.mark.asyncio
async def test_a_shazam_match_on_another_track_rebuilds_the_timeline(tmp_path: Path):
    state = _following("B9", started_s_ago=100)
    orch = _orch(state)
    started = _iso(_ago(12))
    result = {
        "ts": _iso(_ago(0)), "artist": "Failure", "album": "Fantastic Planet",
        "title": "The Nurse Who Loved Me", "track_position": "C13",
        "release_id": RID, "match_method": "shazam", "tracklist": TRACKS,
        "track_started_at": started,
    }

    with _catalog(), _history(), mock.patch(
        "nowplaying.orchestrator._publish_enrichment._art_url_for_release",
        return_value="/art/31427573",
    ):
        await orch._publish_shazam_match(result, "vinyl", tmp_path / "c.wav", -20.0)

    tl = state.side_timeline
    assert (tl.position, tl.side) == ("C13", "C")
    assert _iso(tl.started_at) == started
    assert tl.by_run_out is False


@pytest.mark.asyncio
async def test_a_match_on_the_current_track_leaves_the_timeline_alone(tmp_path: Path):
    state = _following("B9", started_s_ago=100)
    started = state.side_timeline.started_at
    orch = _orch(state)
    result = {
        "ts": _iso(_ago(0)), "artist": "Failure", "album": "Fantastic Planet",
        "title": "Solaris", "track_position": "B9",
        "release_id": RID, "match_method": "shazam", "tracklist": TRACKS,
    }

    with _catalog(), _history(), mock.patch(
        "nowplaying.orchestrator._publish_enrichment._art_url_for_release",
        return_value="/art/31427573",
    ):
        await orch._publish_shazam_match(result, "vinyl", tmp_path / "c.wav", -20.0)

    assert state.side_timeline.position == "B9"
    assert state.side_timeline.started_at == started
    assert _published(orch)[-1]["track_started_at"] == _iso(started)


@pytest.mark.asyncio
async def test_a_match_with_no_track_position_ends_the_timeline(tmp_path: Path):
    state = _following("B9", started_s_ago=100)
    orch = _orch(state)
    result = {
        "ts": _iso(_ago(0)), "artist": "Failure", "album": "Fantastic Planet",
        "title": "Solaris", "release_id": RID, "match_method": "shazam",
    }

    with _catalog(), _history(), mock.patch(
        "nowplaying.orchestrator._publish_enrichment._art_url_for_release",
        return_value="/art/31427573",
    ):
        await orch._publish_shazam_match(result, "vinyl", tmp_path / "c.wav", -20.0)

    assert state.side_timeline is None


@pytest.mark.asyncio
async def test_track_guess_position_comes_from_the_timeline():
    state = _following("A3", started_s_ago=20)
    state.predicted_position.pop("track_started_at")
    orch = _orch(state)
    side_a = [t for t in TRACKS if t["side"] == "A"]

    est = orch._estimate_side_position_s(state, side_a, 1.0)

    assert abs(est - (268 + 245 + 20)) <= 1


@pytest.mark.asyncio
async def test_idle_clears_the_timeline():
    state = _following("B9", started_s_ago=100)
    state.quiet_began_at = _ago(50)
    orch = _orch(state)

    await orch._idle_after_delay(0, "vinyl")

    assert state.side_timeline is None
    assert state.quiet_began_at is None


def test_source_change_clears_the_timeline():
    state = State()
    state.side_timeline = side_timeline.start_at(TRACKS, "B9", _ago(10), RID, None)
    state.quiet_began_at = _ago(5)

    state.reset_vinyl_session()

    assert state.side_timeline is None
    assert state.quiet_began_at is None


def test_a_pick_of_another_track_restarts_the_timeline_there():
    """Identify, pin, next-track and select-release all end here."""
    from nowplaying.control._shared import _follow_pick_on_timeline

    state = State()
    state.sonos_source = "vinyl"
    state.side_timeline = side_timeline.start_at(TRACKS, "B6", _ago(300), RID, None)
    state.last_vinyl = {
        "source": "vinyl", "release_id": RID, "track_position": "B8",
        "side": "B", "title": "Dirty Blue Balloons",
        "track_started_at": _iso(_ago(900)),
    }
    state.track_started_at = _iso(_ago(20))

    with _catalog():
        _follow_pick_on_timeline(state)

    assert state.side_timeline.position == "B8"
    assert _iso(state.side_timeline.started_at) == state.track_started_at
    assert state.last_vinyl["track_started_at"] == state.track_started_at


def test_a_pick_of_the_current_track_keeps_the_timelines_start():
    from nowplaying.control._shared import _follow_pick_on_timeline

    state = State()
    state.sonos_source = "vinyl"
    started = _ago(120)
    state.side_timeline = side_timeline.start_at(TRACKS, "B6", started, RID, None)
    state.last_vinyl = {
        "source": "vinyl", "release_id": RID, "track_position": "B6",
        "side": "B", "title": "Blank", "track_started_at": _iso(_ago(900)),
    }
    state.track_started_at = _iso(_ago(30))

    with _catalog():
        _follow_pick_on_timeline(state)

    assert state.side_timeline.started_at == started
    assert state.track_started_at == _iso(started)
    assert state.last_vinyl["track_started_at"] == _iso(started)
