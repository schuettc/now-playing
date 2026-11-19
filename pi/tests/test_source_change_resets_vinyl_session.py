"""A source change ends the vinyl session: nothing recognised, pinned or
predicted under one source may be applied to the next.
"""
from __future__ import annotations

import asyncio
from unittest import mock

from nowplaying.main import Orchestrator, State


def _run(coro):
    return asyncio.run(coro)


def _make_orch(state: State) -> Orchestrator:
    bcast = mock.MagicMock()
    bcast.publish = mock.AsyncMock(return_value=True)
    return Orchestrator(
        state=state, bcast=bcast, sonos_coord=None,
        stop=mock.MagicMock(), llm=mock.MagicMock(enabled=False),
    )


def _event(source: str, **fields) -> dict:
    return {
        "ts": "2026-11-01T12:00:00Z", "state": "PLAYING",
        "source": source, **fields,
    }


def _dispatch(orch: Orchestrator, ev: dict) -> None:
    with (
        mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()),
        mock.patch(
            "nowplaying.orchestrator._sonos_handlers.signal_capture",
            return_value=True,
        ),
    ):
        _run(orch.on_sonos_event(ev))


def test_vinyl_to_airplay_drops_pin_anchor_and_prediction():
    state = State()
    state.sonos_source = "vinyl"
    state.last_vinyl = {
        "source": "vinyl", "title": "Pitiful", "release_id": 1,
        "track_position": "A2", "side": "A",
    }
    state.user_track_pin = {
        "release_id": 1, "track_position": "A2",
        "monotonic_ts": 1.0, "duration_seconds": None,
    }
    state.pin_different_track_streak = 1
    state.fingerprint_anchor = {"release_id": 1, "track_position": "A2"}
    state.predicted_position = {"release_id": 1, "track_position": "A3"}
    state.unmatched_streak = 3
    state.last_vinyl_confidence_set_at = 5.0
    state.last_shazam_match_unix_ts = 6
    orch = _make_orch(state)

    _dispatch(orch, _event("airplay"))

    assert state.user_track_pin is None
    assert state.pin_different_track_streak == 0
    assert state.fingerprint_anchor is None
    assert state.predicted_position is None
    assert state.unmatched_streak == 0
    assert state.last_vinyl_confidence_set_at is None
    assert state.last_shazam_match_unix_ts is None
    assert state.last_vinyl is None


def test_airplay_track_is_not_the_album_lock_when_vinyl_starts():
    state = State()
    orch = _make_orch(state)
    enriched = {"release_id": 77, "track_position": "A3", "side": "A"}
    with mock.patch.object(
        Orchestrator, "_enrich_sonos_with_discogs",
        lambda self, p: {**p, **enriched} if p.get("title") else p,
    ):
        _dispatch(orch, _event(
            "airplay", title="Glass Onion", artist="The Beatles",
        ))
        assert state.last_vinyl["release_id"] == 77

        _dispatch(orch, _event("vinyl"))

    assert state.last_vinyl is None


def test_predicted_advance_refuses_a_lock_from_another_source():
    state = State()
    state.last_vinyl = {
        "source": "airplay", "release_id": 77,
        "track_position": "A3", "side": "A",
    }
    with mock.patch(
        "nowplaying.orchestrator._prediction_advance.catalog_dispatch.get_release",
        return_value={"tracks": [{"position": "A3", "side": "A"}]},
    ):
        assert Orchestrator._load_locked_tracks(state) is None
