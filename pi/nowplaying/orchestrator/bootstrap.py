"""Orchestrator runtime bootstrap — env-gated feature init, aiohttp app
build, and the ``main_async`` coroutine that systemd ultimately runs via
``python -m nowplaying.main``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from nowplaying import art_overrides, control, history
from nowplaying.api import make_app, serve
from nowplaying.llm import LLMAssist
from nowplaying.sonos.listener import (
    emit_initial_state,
    find_zone,
    get_zone_name,
    run_listener,
)
from nowplaying.discovery import init_db as init_discovered_db
from nowplaying.vinyl import fingerprint
from nowplaying.vinyl.runtime import run_capture_supervised

from nowplaying.orchestrator._class import Orchestrator
from nowplaying.orchestrator.state import State
from nowplaying.orchestrator.streaming_idle import _should_pause_capture

log = logging.getLogger("nowplaying.main")

REPO_ROOT = Path(__file__).resolve().parents[3]
PI_DIR = REPO_ROOT / "pi"


async def _init_optional_features() -> tuple[LLMAssist, bool]:
    """Read env-gated optional-feature toggles and warm any side schemas.
    Returns (llm, fingerprint_enabled). FINGERPRINT_ENABLED gates the F3
    cascade; LLMAssist self-gates on ANTHROPIC_API_KEY.
    """
    llm = LLMAssist()
    fingerprint_enabled = (
        os.environ.get("FINGERPRINT_ENABLED", "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    log.info(
        "features: fingerprint=%s llm=%s",
        "on" if fingerprint_enabled else "off",
        "on" if llm.enabled else "off",
    )
    if fingerprint_enabled:
        await asyncio.to_thread(fingerprint.init_db)
    # discovered.sqlite is always created — the discovery cascade fires
    # on any Shazam-only branch and persistence requires the schema.
    await asyncio.to_thread(init_discovered_db)
    return llm, fingerprint_enabled


async def _build_app(state: "State", llm: LLMAssist):
    """Build the aiohttp app, wire control + history routes BEFORE serve()
    freezes the router, and return (app, runner, bcast).
    """
    app = make_app()
    app["state"] = state
    app["llm"] = llm  # Route handlers (control.py /identify) reach LLM here.
    control.register(app)
    history.init_db()
    history.register(app)
    _app, runner = await serve(app=app)
    return app, runner, app["broadcaster"]


async def main_async() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    load_dotenv(PI_DIR / ".env")

    llm, fingerprint_enabled = await _init_optional_features()

    # Pre-warm the art-overrides index cache so the first Sonos event
    # after boot doesn't pay a synchronous disk read on the event loop
    # inside `_rewrite_art_url_for_overrides`.
    await asyncio.to_thread(art_overrides.prewarm)

    state = State()
    app, runner, bcast = await _build_app(state, llm)

    zone = get_zone_name()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # Pre-discover the Sonos coordinator so the airplay re-poll loop can
    # poll without re-running zone discovery on every tick.
    sonos_coord = await asyncio.get_running_loop().run_in_executor(
        None, find_zone, zone,
    )
    if sonos_coord is None:
        log.warning(
            "sonos zone %r not discovered at startup; "
            "airplay repoll loop will be skipped until next restart",
            zone,
        )

    orch = Orchestrator(
        state=state, bcast=bcast, sonos_coord=sonos_coord, stop=stop, llm=llm,
        fingerprint_enabled=fingerprint_enabled,
    )

    try:
        await emit_initial_state(zone, orch.on_sonos_event)
    except Exception as e:
        log.warning("initial state probe failed: %r", e)

    listener_task = asyncio.create_task(run_listener(zone, orch.on_sonos_event, stop))
    sonos_repoll_task = asyncio.create_task(orch.sonos_repoll_loop())
    # Start capture with the right emit state for the initial Sonos source.
    # emit_initial_state above has already populated state.sonos_source +
    # state.sonos_has_metadata, so we know whether to start paused without
    # needing to signal post-startup (avoids the race where signal_capture
    # fires before _capture_pid is registered).
    initial_should_pause = _should_pause_capture(state)
    state.capture_emit_paused = initial_should_pause
    capture_task = asyncio.create_task(
        run_capture_supervised(
            orch.on_heartbeat, orch.on_capture_state, stop,
            get_start_paused=lambda: _should_pause_capture(state),
            start_paused=initial_should_pause,
        )
    )
    hygiene_task = asyncio.create_task(orch.hygiene_loop())

    try:
        await stop.wait()
    finally:
        await asyncio.gather(listener_task, capture_task, hygiene_task, sonos_repoll_task, return_exceptions=True)
        await runner.cleanup()
        log.info("shutdown complete")
