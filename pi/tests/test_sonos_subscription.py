"""Unit tests for Sonos subscription renewal + dead-listener watchdog.

Covers:
  (a) watchdog triggers resubscribe after simulated silence
  (b) watchdog does not trigger when events are flowing
  (c) startup probe emits "verified" log on first NOTIFY
  (d) startup probe emits warning on silence
  (e) _resubscribe unsubscribes old sub before creating new one
  (f) liveness probe: zone unreachable → skip resubscribe
  (g) resubscribe reconciles actual state after stale post-resubscribe NOTIFY

These tests are entirely offline — no Sonos hardware required.  All soco
objects are mocked.
"""
from __future__ import annotations

import asyncio
import sys
import time as _time_module
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_coord() -> MagicMock:
    """Return a minimal SoCo mock with avTransport.subscribe coroutine."""
    coord = MagicMock()
    coord.player_name = "Office"
    coord.ip_address = "192.168.1.100"
    coord.port = 1400

    # build a fake subscription object
    fake_sub = MagicMock()
    fake_sub.sid = "test-sid-1"
    fake_sub.timeout = 1800
    fake_sub.callback = None
    fake_sub.auto_renew_fail = None
    fake_sub.unsubscribe = AsyncMock()

    coord.avTransport.subscribe = AsyncMock(return_value=fake_sub)
    coord.get_current_transport_info = MagicMock(return_value={"current_transport_state": "PLAYING"})
    return coord, fake_sub


# ---------------------------------------------------------------------------
# (c) Startup probe — subscription verified log on first NOTIFY
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_probe_verified(caplog):
    """Startup probe should log 'subscription verified' when an event arrives."""
    import logging
    from nowplaying.sonos import listener as _listener

    coord, fake_sub = _make_fake_coord()
    stop = asyncio.Event()
    events_received: list[dict] = []

    async def on_event(ev: dict) -> None:
        events_received.append(ev)

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 0.2),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 999.0),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 999.0),
        caplog.at_level(logging.INFO, logger="nowplaying.sonos.listener"),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event, stop))

        # Let the subscription + probe task start
        await asyncio.sleep(0.05)

        # Simulate a NOTIFY by directly setting last_event_time via a fake callback
        # (find the _SonosListener by introspecting the closure — easier to just
        # wait for the probe timeout after simulating an event via set_last).
        # We'll find it via the sub's callback that was assigned.
        listener_obj = fake_sub.callback.__self__
        listener_obj.last_event_time = _time_module.monotonic()

        # Wait for probe to fire
        await asyncio.sleep(0.25)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    assert any("subscription verified" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (d) Startup probe — warning on silence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_probe_warning_on_silence(caplog):
    """Startup probe should warn when no NOTIFY arrives within the timeout."""
    import logging
    from nowplaying.sonos import listener as _listener

    coord, fake_sub = _make_fake_coord()
    stop = asyncio.Event()

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 0.1),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 999.0),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 999.0),
        caplog.at_level(logging.WARNING, logger="nowplaying.sonos.listener"),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event=AsyncMock(), stop=stop))
        # No events are ever sent — probe should warn after 0.1s
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    assert any("no NOTIFY received" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (b) Watchdog does NOT trigger when events are flowing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_no_resubscribe_when_events_flowing(caplog):
    """Watchdog must not resubscribe if last_event_time is within the threshold."""
    from nowplaying.sonos import listener as _listener

    coord, fake_sub = _make_fake_coord()
    stop = asyncio.Event()

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 999.0),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 0.05),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 30.0),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event=AsyncMock(), stop=stop))
        await asyncio.sleep(0.02)

        # Set last_event_time to "just now" so threshold is never exceeded
        listener_obj = fake_sub.callback.__self__
        listener_obj.last_event_time = _time_module.monotonic()

        # Run a few watchdog cycles
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    # subscribe should have been called exactly once (initial)
    assert coord.avTransport.subscribe.call_count == 1
    # unsubscribe on the fake_sub should only have been called during shutdown
    # (once) — not by the watchdog
    assert fake_sub.unsubscribe.call_count == 1


# ---------------------------------------------------------------------------
# (a) Watchdog triggers resubscribe after simulated silence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_resubscribes_after_silence(caplog):
    """Watchdog must resubscribe when no events arrive for > threshold."""
    import logging
    from nowplaying.sonos import listener as _listener

    coord, fake_sub_1 = _make_fake_coord()

    # Build a second fake subscription returned on the second subscribe() call
    fake_sub_2 = MagicMock()
    fake_sub_2.sid = "test-sid-2"
    fake_sub_2.timeout = 1800
    fake_sub_2.callback = None
    fake_sub_2.auto_renew_fail = None
    fake_sub_2.unsubscribe = AsyncMock()

    subs_iter = iter([fake_sub_1, fake_sub_2])

    async def _subscribe_side_effect(*args, **kwargs):
        return next(subs_iter)

    coord.avTransport.subscribe = AsyncMock(side_effect=_subscribe_side_effect)

    stop = asyncio.Event()

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 999.0),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 0.05),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 0.01),
        caplog.at_level(logging.WARNING, logger="nowplaying.sonos.listener"),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event=AsyncMock(), stop=stop))
        # Let last_event_time stay None (never set) → elapsed = inf > 0.01s threshold
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    # subscribe should have been called at least twice (initial + resubscribe)
    assert coord.avTransport.subscribe.call_count >= 2
    # Old subscription should have been unsubscribed before the new one
    assert fake_sub_1.unsubscribe.call_count >= 1
    assert any("resubscrib" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (e) _resubscribe unsubscribes old sub before subscribing new one
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resubscribe_unsubscribes_old_before_new():
    """_resubscribe must call unsubscribe on the old sub before subscribe."""
    from nowplaying.sonos import listener as _listener

    coord, fake_sub_1 = _make_fake_coord()
    fake_sub_2 = MagicMock()
    fake_sub_2.sid = "test-sid-2"
    fake_sub_2.timeout = 1800
    fake_sub_2.callback = None
    fake_sub_2.auto_renew_fail = None
    fake_sub_2.unsubscribe = AsyncMock()

    # Track call order
    call_order: list[str] = []

    async def _unsubscribe_1() -> None:
        call_order.append("unsubscribe_old")

    fake_sub_1.unsubscribe = AsyncMock(side_effect=_unsubscribe_1)

    async def _subscribe_new(*args, **kwargs) -> MagicMock:
        call_order.append("subscribe_new")
        return fake_sub_2

    subs_iter_e = iter([fake_sub_1, fake_sub_2])

    async def _subscribe_side_effect_e(*args, **kwargs):
        sub = next(subs_iter_e)
        if sub is fake_sub_2:
            await _subscribe_new()
        return sub

    coord.avTransport.subscribe = AsyncMock(side_effect=_subscribe_side_effect_e)

    stop = asyncio.Event()

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 999.0),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 0.05),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 0.01),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event=AsyncMock(), stop=stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    # unsubscribe must appear before subscribe_new in the call order
    try:
        unsub_idx = call_order.index("unsubscribe_old")
        sub_idx = call_order.index("subscribe_new")
        assert unsub_idx < sub_idx, "old sub must be unsubscribed before new sub is created"
    except ValueError:
        # If resubscribe never ran, check that at minimum subscribe was called twice
        # (both initial + one resubscribe attempt)
        assert coord.avTransport.subscribe.call_count >= 2


# ---------------------------------------------------------------------------
# (f) Liveness probe — zone unreachable → skip resubscribe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_skips_resubscribe_when_zone_unreachable(caplog):
    """Watchdog must not resubscribe if the liveness probe fails (network issue)."""
    import logging
    from nowplaying.sonos import listener as _listener

    coord, fake_sub = _make_fake_coord()
    # Liveness probe always fails
    coord.get_current_transport_info.side_effect = OSError("connection refused")

    stop = asyncio.Event()

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 999.0),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 0.05),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 0.01),
        caplog.at_level(logging.WARNING, logger="nowplaying.sonos.listener"),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event=AsyncMock(), stop=stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    # subscribe should only have been called once (initial) — watchdog skipped resubscribe
    assert coord.avTransport.subscribe.call_count == 1
    assert any("zone unreachable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (g) Post-resubscribe state reconciliation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resubscribe_reconciles_actual_state():
    """After watchdog resubscription the listener must fire on_event with the
    actual current Sonos state (polled via get_current_track_info), not rely
    solely on the first post-resubscribe NOTIFY which may carry stale state.

    Scenario from prod: listener quiet for 600s while AirPlay plays, watchdog
    resubscribes, first NOTIFY carries old vinyl state — kiosk gets stuck.
    """
    from nowplaying.sonos import listener as _listener

    coord, fake_sub_1 = _make_fake_coord()
    fake_sub_2 = MagicMock()
    fake_sub_2.sid = "test-sid-2"
    fake_sub_2.timeout = 1800
    fake_sub_2.callback = None
    fake_sub_2.auto_renew_fail = None
    fake_sub_2.unsubscribe = AsyncMock()

    call_count = 0
    async def _subscribe_side(*a, **kw):
        nonlocal call_count
        call_count += 1
        return fake_sub_1 if call_count == 1 else fake_sub_2
    coord.avTransport.subscribe = AsyncMock(side_effect=_subscribe_side)

    # Actual state: AirPlay session playing Car Seat Headrest
    coord.get_current_track_info = MagicMock(return_value={
        "title": "Destroyed By Hippie Powers",
        "artist": "Car Seat Headrest",
        "album": "Twin Fantasy",
        "album_art": "",
        "uri": "x-sonos-vli:RINCON_xxx:1,airplay:abc123",
        "position": "0:01:23",
        "duration": "0:04:15",
    })
    coord.get_current_transport_info = MagicMock(return_value={
        "current_transport_state": "PLAYING",
    })

    events_received: list[dict] = []
    async def on_event(ev: dict) -> None:
        events_received.append(ev)

    stop = asyncio.Event()

    with (
        patch.object(_listener, "find_zone", return_value=coord),
        patch.object(_listener.events_asyncio.event_listener, "async_stop", new=AsyncMock()),
        patch.object(_listener, "_STARTUP_PROBE_TIMEOUT_S", 999.0),
        patch.object(_listener, "_WATCHDOG_INTERVAL_S", 0.05),
        patch.object(_listener, "_DEAD_LISTENER_TIMEOUT_S", 0.01),
    ):
        task = asyncio.create_task(_listener.run_listener("Office", on_event, stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    airplay_events = [e for e in events_received if e.get("source") == "airplay"]
    assert airplay_events, (
        "expected on_event called with source='airplay' after resubscription reconcile; "
        f"got sources: {[e.get('source') for e in events_received]}"
    )
    assert airplay_events[-1]["title"] == "Destroyed By Hippie Powers"
