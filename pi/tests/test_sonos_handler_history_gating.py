"""on_sonos_event must record a broadcaster-suppressed publish as
extend-only, so a duplicate airplay NOTIFY extends the current play
instead of inserting a new history row.

Drives on_sonos_event through a REAL Broadcaster (the suppression verdict
is the whole point — mocking it would test nothing) with the enrichment
chain stubbed to identity, since those helpers are covered elsewhere.
"""
from __future__ import annotations

import asyncio
from unittest import mock


from nowplaying.api.broadcaster import Broadcaster


def _run(coro):
    return asyncio.run(coro)


def _make_orch():
    from nowplaying.main import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.state = mock.MagicMock()
    o.state.sonos_source = "airplay"
    o.bcast = Broadcaster()
    o._anchor_and_publish = lambda p: p
    for name in (
        "_maybe_cancel_idle_on_resume", "_reconcile_capture_emit",
        "_reset_for_non_idle_source", "_maybe_arm_streaming_idle",
        "_apply_source_transition",
    ):
        setattr(o, name, mock.MagicMock())
    o._handle_sticky_idle = lambda p: False
    o._recover_unmatched_from_cache = lambda p: p
    o._enrich_sonos_with_discogs = lambda p: p
    o._rewrite_art_url_for_overrides = lambda p: p
    o._enrich_with_queue = mock.AsyncMock(side_effect=lambda p: p)
    return o


def _event(title="Crooked Teeth"):
    return {
        "ts": "2026-08-26T03:06:49Z",
        "state": "PLAYING",
        "source": "airplay",
        "title": title,
        "artist": "Death Cab for Cutie",
        "album": "Plans",
        "sonos_polled": True,
    }


def test_sonos_publish_rekeys_service_art_after_release_match():
    """An identified LP2 and LP4 cannot share an artist+album proxy image."""
    o = _make_orch()
    o._enrich_sonos_with_discogs = lambda p: {**p, "release_id": 9191767 if p["title"] == "LP2 song" else 99999}
    lp2 = {**_event(title="LP2 song"), "artist": "American Football",
           "album": "American Football", "album_art": "http://sonos:1400/getaa?u=lp2"}
    lp4 = {**lp2, "title": "LP4 song", "album_art": "http://sonos:1400/getaa?u=lp4"}
    with mock.patch("nowplaying.history.record_play", new=mock.AsyncMock()):
        _run(o.on_sonos_event(lp2))
        first = o.bcast._last["art_url"]
        _run(o.on_sonos_event(lp4))
        second = o.bcast._last["art_url"]
    assert first.startswith("/art-cache/")
    assert second.startswith("/art-cache/")
    assert first.split("?")[0] != second.split("?")[0]
    assert "u=http%3A%2F%2Fsonos%3A1400%2Fgetaa%3Fu%3Dlp2" in first
    assert "u=http%3A%2F%2Fsonos%3A1400%2Fgetaa%3Fu%3Dlp4" in second


def test_suppressed_publish_records_extend_only():
    o = _make_orch()
    with mock.patch(
        "nowplaying.history.record_play", new=mock.AsyncMock(),
    ) as rec:
        _run(o.on_sonos_event(_event()))
        _run(o.on_sonos_event(_event()))
    assert rec.await_args_list[0].kwargs.get("extend_only") is False
    assert rec.await_args_list[1].kwargs.get("extend_only") is True


def test_track_change_records_new_row():
    o = _make_orch()
    with mock.patch(
        "nowplaying.history.record_play", new=mock.AsyncMock(),
    ) as rec:
        _run(o.on_sonos_event(_event(title="Track A")))
        _run(o.on_sonos_event(_event(title="Track B")))
    assert rec.await_args_list[0].kwargs.get("extend_only") is False
    assert rec.await_args_list[1].kwargs.get("extend_only") is False
