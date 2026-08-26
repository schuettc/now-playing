"""The sonos repoll loop must recover an AirPlay session that opened with
an empty DIDL.

Sonos delivers ``state=PLAYING source=airplay`` with no title/artist at
session open, then fills the metadata into its own device state a beat
later without necessarily sending another NOTIFY. The repoll loop is the
only periodic mechanism that can pull that late metadata across, so it
must run for a metadata-less airplay source — not gate itself off on the
very flag it would populate.

Guards the two halves that keep the fix from over-correcting:
  - the source gate (vinyl/tv/radio/unknown must never be repolled)
  - the no-title guard (a genuinely metadata-less source is a no-op)
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest


def _run(coro):
    return asyncio.run(coro)


TRACK = {
    "title": "Someday You Will Be Loved",
    "artist": "Death Cab for Cutie",
    "album": "Plans",
    "album_art": "http://192.168.5.213:1400/getaa?u=1",
    "duration_s": 191,
}


def _make_orch(source: str, *, has_metadata: bool, last_vinyl=None):
    from nowplaying.main import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.state = mock.MagicMock()
    o.state.sonos_source = source
    o.state.sonos_has_metadata = has_metadata
    o.state.last_vinyl = last_vinyl
    o.on_sonos_event = mock.AsyncMock()
    return o


def test_repoll_recovers_airplay_that_started_without_metadata():
    """The red test: metadata-less airplay must still poll and dispatch."""
    o = _make_orch("airplay", has_metadata=False, last_vinyl=None)
    with mock.patch(
        "nowplaying.orchestrator._publish_enrichment.poll_track",
        new=mock.AsyncMock(return_value=dict(TRACK)),
    ):
        _run(o._run_repoll_tick(mock.MagicMock()))
    o.on_sonos_event.assert_awaited_once()
    ev = o.on_sonos_event.await_args.args[0]
    assert ev["title"] == "Someday You Will Be Loved"
    assert ev["artist"] == "Death Cab for Cutie"
    assert ev["source"] == "airplay"


def test_repoll_polls_even_when_has_metadata_is_false():
    """Isolates 'gate is gone' from 'dispatch worked'."""
    o = _make_orch("airplay", has_metadata=False, last_vinyl=None)
    poll = mock.AsyncMock(return_value=dict(TRACK))
    with mock.patch(
        "nowplaying.orchestrator._publish_enrichment.poll_track", new=poll,
    ):
        _run(o._run_repoll_tick(mock.MagicMock()))
    poll.assert_awaited_once()


def test_repoll_noop_when_sonos_genuinely_has_no_metadata():
    """True system-audio airplay: Sonos returns no title. The downstream
    no-title guard — not the deleted metadata gate — is what keeps this a
    no-op."""
    o = _make_orch("airplay", has_metadata=False, last_vinyl=None)
    with mock.patch(
        "nowplaying.orchestrator._publish_enrichment.poll_track",
        new=mock.AsyncMock(return_value=None),
    ):
        _run(o._run_repoll_tick(mock.MagicMock()))
    o.on_sonos_event.assert_not_awaited()


@pytest.mark.parametrize("source", ["vinyl", "tv", "radio", "unknown", None])
def test_repoll_skipped_for_non_sonos_sources(source):
    """The surviving source gate: only airplay/streaming are ever repolled."""
    o = _make_orch(source, has_metadata=False, last_vinyl=None)
    poll = mock.AsyncMock(return_value=dict(TRACK))
    with mock.patch(
        "nowplaying.orchestrator._publish_enrichment.poll_track", new=poll,
    ):
        _run(o._run_repoll_tick(mock.MagicMock()))
    poll.assert_not_awaited()
    o.on_sonos_event.assert_not_awaited()


def test_repoll_does_not_republish_unchanged_airplay_track():
    """Steady-state airplay tick with no track change dispatches nothing."""
    o = _make_orch(
        "airplay", has_metadata=True,
        last_vinyl={"title": TRACK["title"], "artist": TRACK["artist"]},
    )
    with mock.patch(
        "nowplaying.orchestrator._publish_enrichment.poll_track",
        new=mock.AsyncMock(return_value=dict(TRACK)),
    ):
        _run(o._run_repoll_tick(mock.MagicMock()))
    o.on_sonos_event.assert_not_awaited()


def test_repoll_still_reemits_unchanged_streaming_for_queue_advance():
    """Streaming re-emits even on an unchanged track so queue position
    can advance — the deliberate asymmetry with airplay."""
    o = _make_orch(
        "streaming", has_metadata=True,
        last_vinyl={"title": TRACK["title"], "artist": TRACK["artist"]},
    )
    with mock.patch(
        "nowplaying.orchestrator._publish_enrichment.poll_track",
        new=mock.AsyncMock(return_value=dict(TRACK)),
    ):
        _run(o._run_repoll_tick(mock.MagicMock()))
    o.on_sonos_event.assert_awaited_once()
