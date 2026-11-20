"""A Shazam match with no release in the collection cannot take over the
screen while a Discogs release's side is being followed.

2026-11-20 09:52: during "Segue 1" (A3 of Failure, *Fantastic Planet*,
release 31427573) Shazam returned Alain Goraguer, "Les Fusées" three times
15 s apart; two agreeing results were enough to replace the album.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from nowplaying.orchestrator import side_timeline
from nowplaying.orchestrator._class import Orchestrator
from nowplaying.orchestrator.state import State

RID = 31427573
SIDE_A = [
    {"position": "A1", "side": "A", "title": "Saturday Savior", "duration_seconds": 268},
    {"position": "A2", "side": "A", "title": "Sergeant Politeness", "duration_seconds": 245},
    {"position": "A3", "side": "A", "title": "Segue 1", "duration_seconds": 114},
    {"position": "A4", "side": "A", "title": "Smoking Umbrellas", "duration_seconds": 239},
]
RELEASE = {"id": RID, "tracks": SIDE_A}
LES_FUSEES = {
    "artist": "Alain Goraguer", "title": "Les Fusées",
    "release_id": None, "match_method": "shazam",
}
MUSIC = -20.0


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _following(position: str | None, started_s_ago: float = 0.0) -> State:
    s = State()
    s.sonos_source = "vinyl"
    s.last_vinyl = {
        "source": "vinyl", "release_id": RID, "artist": "Failure",
        "album": "Fantastic Planet", "title": "Sergeant Politeness",
        "side": "A", "track_position": "A2", "tracklist": SIDE_A,
        "match_method": "shazam",
    }
    if position is not None:
        s.side_timeline = side_timeline.start_at(
            SIDE_A, position, _ago(started_s_ago), RID, None,
        )
    return s


def _orch(state: State) -> Orchestrator:
    bcast = mock.AsyncMock()
    bcast.publish = mock.AsyncMock(return_value=True)
    return Orchestrator(
        state=state, bcast=bcast, sonos_coord=None, stop=asyncio.Event(),
        llm=mock.AsyncMock(enabled=False), fingerprint_enabled=False,
    )


@pytest.mark.asyncio
async def test_offcollection_match_is_ignored_mid_side(tmp_path: Path, caplog):
    state = _following("A3", started_s_ago=40)
    orch = _orch(state)

    with caplog.at_level(logging.INFO, logger="nowplaying.main"), mock.patch(
        "nowplaying.history.record_play", new=mock.AsyncMock(),
    ):
        for _ in range(3):
            await orch._publish_shazam_match(
                dict(LES_FUSEES), "vinyl", tmp_path / "clip.wav", MUSIC,
            )

    orch.bcast.publish.assert_not_called()
    assert state.last_vinyl["album"] == "Fantastic Planet"
    assert state.side_timeline.position == "A3"
    assert state.pending_shazam_only == []
    ignored = [r.getMessage() for r in caplog.records if "shazam-only ignored" in r.getMessage()]
    assert ignored == [
        "shazam-only ignored: following release=31427573 side=A until the "
        "side ends (artist='Alain Goraguer' title='Les Fusées')"
    ]


@pytest.mark.asyncio
async def test_offcollection_match_takes_over_after_the_side_ends(tmp_path: Path):
    state = _following("A4", started_s_ago=249)  # 239 s listed: ended 10 s ago
    orch = _orch(state)
    clip = tmp_path / "clip.wav"

    assert orch._shazam_only_gate_passes(MUSIC, clip, dict(LES_FUSEES)) is False
    assert orch._shazam_only_gate_passes(MUSIC, clip, dict(LES_FUSEES)) is True


@pytest.mark.asyncio
async def test_identify_tap_bypasses_the_gate(tmp_path: Path):
    state = _following("A3", started_s_ago=40)
    orch = _orch(state)

    assert orch._shazam_only_gate_passes(
        MUSIC, tmp_path / "clip_instant.wav", dict(LES_FUSEES),
    ) is True


@pytest.mark.asyncio
async def test_match_for_another_owned_record_still_takes_over(tmp_path: Path):
    state = _following("A3", started_s_ago=40)
    orch = _orch(state)
    owned = {
        "artist": "Hum", "album": "You'd Prefer an Astronaut", "title": "Stars",
        "release_id": 1234, "track_position": "A3", "match_method": "shazam",
    }

    with mock.patch(
        "nowplaying.catalog.get_release", return_value=None,
    ), mock.patch(
        "nowplaying.history.record_play", new=mock.AsyncMock(),
    ), mock.patch(
        "nowplaying.orchestrator._publish_enrichment._art_url_for_release",
        return_value="/art/1234",
    ):
        await orch._publish_shazam_match(owned, "vinyl", tmp_path / "clip.wav", MUSIC)

    orch.bcast.publish.assert_called_once()
    assert state.last_vinyl["release_id"] == 1234


@pytest.mark.asyncio
async def test_gate_is_unchanged_with_no_timeline(tmp_path: Path):
    state = _following(None)
    orch = _orch(state)
    clip = tmp_path / "clip.wav"

    assert orch._shazam_only_gate_passes(MUSIC, clip, dict(LES_FUSEES)) is False
    assert orch._shazam_only_gate_passes(MUSIC, clip, dict(LES_FUSEES)) is True


@pytest.mark.asyncio
async def test_ignored_match_still_lets_the_track_run_out(tmp_path: Path):
    """09:52:20: "Les Fusées" arrived 5 s after A2's listed end. The ignored
    heartbeat still moves the timeline on to A3, "Segue 1"."""
    state = _following("A2", started_s_ago=250)
    state.last_vinyl_confidence_set_at = asyncio.get_running_loop().time() - 250
    orch = _orch(state)

    with mock.patch(
        "nowplaying.catalog.get_release", return_value=RELEASE,
    ), mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()):
        await orch._publish_shazam_match(
            dict(LES_FUSEES), "vinyl", tmp_path / "clip.wav", MUSIC,
        )

    published = [c.args[0] for c in orch.bcast.publish.call_args_list]
    assert [p["title"] for p in published] == ["Segue 1"]
    assert state.last_vinyl["album"] == "Fantastic Planet"
