"""Sonos UPnP listener — promoted from scripts/listener_proto.py.

Subscribes to AVTransport events on the configured zone coordinator and yields
unified now-playing dicts. Designed to be embedded in the orchestrator.

Subscription lifecycle
-----------------------
The orchestrator depends on a continuous stream of UPnP AVTransport events.
UPnP subscriptions carry a finite lease (Sonos defaults to 30 min); once the
lease expires the device silently stops sending NOTIFY events.  Three layers
of protection are implemented here:

1. ``auto_renew=True`` — soco renews the lease at ~85% of the timeout
   (~25.5 min) automatically.  This is the primary renewal path.
2. Dead-listener watchdog — an asyncio task that checks the last-event
   timestamp every 60 s.  If no events have arrived for
   ``SONOS_DEAD_LISTENER_TIMEOUT_S`` seconds (default 600 / 10 min) the
   watchdog first runs a SOAP liveness probe.  If the probe confirms the
   zone is reachable but events have stopped, it force-resubscribes.
3. Startup probe — a one-shot 30-second check that logs "subscription
   verified" when the first NOTIFY arrives, or warns if none does.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from soco import SoCo, config as soco_config, discover, events_asyncio

from nowplaying.sonos._listener_supervisor import (
    _ListenerSupervisor,
    _SonosListener,
    _poll_current_track,
    _poll_queue_sync,
    classify_uri,
    now_iso,
)

log = logging.getLogger("nowplaying.sonos.listener")

soco_config.EVENTS_MODULE = events_asyncio

# ---------------------------------------------------------------------------
# Subscription-renewal / watchdog constants
# ---------------------------------------------------------------------------
# How long to wait without any NOTIFY before the watchdog probes + possibly
# resubscribes.  Default is 600 s (10 min) — conservative vs the 30-min UPnP
# lease.  Set SONOS_DEAD_LISTENER_TIMEOUT_S=90 in pi/.env to accelerate
# testing on the Pi.
_DEAD_LISTENER_TIMEOUT_S: float = float(
    os.environ.get("SONOS_DEAD_LISTENER_TIMEOUT_S", "600")
)

# Maximum consecutive resubscribe failures before giving up and raising so
# systemd can restart the orchestrator.
_MAX_RESUB_FAILURES = 3

# How long the startup probe waits for the first NOTIFY before warning.
_STARTUP_PROBE_TIMEOUT_S = 30.0

# Watchdog poll interval.
_WATCHDOG_INTERVAL_S = 60.0

EventHandler = Callable[[dict], Awaitable[None] | None]


def find_zone(name: str) -> SoCo | None:
    zones = discover(timeout=5) or set()
    for z in zones:
        if z.player_name == name:
            return z
    return None


async def poll_track(coordinator: SoCo) -> dict | None:
    """Async wrapper around ``_poll_current_track`` for callers outside
    this module. Runs the SOAP call in a thread-pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _poll_current_track, coordinator)


async def poll_queue(coordinator: SoCo, limit: int = 16) -> list[dict]:
    """Async wrapper around ``_poll_queue_sync``. Returns a list of
    ``{title, artist, album}`` dicts; empty list on AirPlay or error."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _poll_queue_sync, coordinator, limit)


def _build_initial_payload(coord: SoCo, info: dict, ti: dict) -> dict:
    uri = info.get("uri") or None
    source, prefix = classify_uri(uri)
    art = info.get("album_art") or None
    if art and art.startswith("/"):
        art = f"http://{coord.ip_address}:1400{art}"
    return {
        "ts": now_iso(),
        "zone": coord.player_name,
        "coordinator_ip": coord.ip_address,
        "state": ti.get("current_transport_state") or "STOPPED",
        "source": source,
        "title": info.get("title") or None,
        "artist": info.get("artist") or None,
        "album": info.get("album") or None,
        "album_art": art,
        "uri": uri,
        "raw_uri_prefix": prefix,
        "duration": info.get("duration"),
    }


async def run_listener(zone_name: str, on_event: EventHandler, stop: asyncio.Event) -> None:
    """Subscribe to AVTransport on the named zone and dispatch events to on_event.

    Runs until ``stop`` is set.  Manages subscription renewal (via soco's
    built-in ``auto_renew``), a dead-listener watchdog, and a startup probe.
    Cleans up the subscription on shutdown.
    """
    loop = asyncio.get_running_loop()

    log.info("[sonos] discovering zone %r...", zone_name)
    coord = await loop.run_in_executor(None, find_zone, zone_name)
    if coord is None:
        raise SystemExit(f"zone {zone_name!r} not found via SSDP discovery")
    log.info("[sonos] subscribing on %s (%s)", coord.player_name, coord.ip_address)

    listener = _SonosListener(coord, on_event, loop)
    sup = _ListenerSupervisor(
        coord, zone_name, on_event, listener, loop, stop,
        dead_timeout=_DEAD_LISTENER_TIMEOUT_S,
        watchdog_interval=_WATCHDOG_INTERVAL_S,
        startup_timeout=_STARTUP_PROBE_TIMEOUT_S,
        max_resub_failures=_MAX_RESUB_FAILURES,
        find_zone_fn=find_zone,
        build_initial_payload_fn=_build_initial_payload,
    )

    await sup.subscribe_once()

    startup_task = asyncio.create_task(sup.startup_probe())
    watchdog_task = asyncio.create_task(sup.watchdog())
    stop_task = asyncio.create_task(stop.wait())

    try:
        done, pending = await asyncio.wait(
            {stop_task, watchdog_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if watchdog_task in done and not watchdog_task.cancelled():
            watchdog_task.result()
    finally:
        startup_task.cancel()
        watchdog_task.cancel()
        stop_task.cancel()
        for label, coro in (
            ("unsubscribe AVTransport", sup.sub_box[0].unsubscribe() if sup.sub_box[0] else _noop()),
            ("stop event listener", events_asyncio.event_listener.async_stop()),
        ):
            try:
                await coro
            except Exception as e:
                log.warning("[sonos] non-fatal cleanup error during %s: %r", label, e)
        log.info("[sonos] shut down cleanly")


async def _noop() -> None:
    """No-op coroutine for cleanup when no subscription exists."""


async def emit_initial_state(zone_name: str, on_event: EventHandler) -> None:
    """Probe the current Sonos state once at startup so the kiosk has something
    to render before the next AVTransport event fires."""
    coord = await asyncio.get_running_loop().run_in_executor(None, find_zone, zone_name)
    if coord is None:
        return
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, coord.get_current_track_info)
        ti = await asyncio.get_running_loop().run_in_executor(None, coord.get_current_transport_info)
    except Exception as e:
        log.warning("[sonos] initial probe failed: %r", e)
        return

    payload = _build_initial_payload(coord, info, ti)
    result = on_event(payload)
    if asyncio.iscoroutine(result):
        await result


def get_zone_name() -> str:
    return os.environ.get("SONOS_ZONE_NAME", "Office")
