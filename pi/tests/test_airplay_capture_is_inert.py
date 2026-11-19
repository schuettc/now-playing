"""AirPlay belongs to Sonos: the Pi does not listen to it, with or without
Sonos metadata, and a capture edge during AirPlay changes nothing.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.main import Orchestrator, State
from nowplaying.orchestrator.streaming_idle import _should_pause_capture


def _state(source: str, *, has_metadata: bool) -> State:
    state = State()
    state.sonos_source = source
    state.sonos_has_metadata = has_metadata
    return state


@pytest.mark.parametrize("has_metadata", [True, False])
def test_capture_is_paused_for_all_airplay(has_metadata):
    assert _should_pause_capture(_state("airplay", has_metadata=has_metadata)) is True


def test_capture_runs_for_vinyl():
    assert _should_pause_capture(_state("vinyl", has_metadata=False)) is False


@pytest.mark.parametrize("edge", ["silent", "audible"])
def test_capture_edge_during_airplay_does_nothing(edge):
    """Capture reported as running (a SIGHUP not yet delivered) must not
    arm the vinyl idle or publish a spinner over the AirPlay track."""
    state = _state("airplay", has_metadata=False)
    state.capture_emit_paused = False
    bcast = mock.MagicMock()
    bcast.publish = mock.AsyncMock()
    orch = Orchestrator(
        state=state, bcast=bcast, sonos_coord=None,
        stop=mock.MagicMock(), llm=mock.MagicMock(enabled=False),
    )

    asyncio.run(orch.on_capture_state(edge))

    assert state.idle_task is None
    bcast.publish.assert_not_awaited()
