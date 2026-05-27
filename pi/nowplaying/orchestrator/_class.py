"""The orchestrator runtime class — wires Sonos / capture / recognizer
event handlers around the shared State.

Composed from five mixins:
  SonosHandlersMixin       — Sonos/capture event handlers
  HeartbeatHandlersMixin   — heartbeat recognition cascade
  PredictionMixin          — unmatched/prediction/pin/idle logic
  LLMHooksMixin            — LLM-assisted decisions
  PublishEnrichmentMixin   — payload enrichment and broadcast helpers
"""
from __future__ import annotations

import asyncio
import logging

from nowplaying.llm import LLMAssist
from nowplaying.vinyl import hygiene
from nowplaying.orchestrator.state import State
from nowplaying.orchestrator._publish_enrichment import PublishEnrichmentMixin
from nowplaying.orchestrator._llm_hooks import LLMHooksMixin
from nowplaying.orchestrator._prediction import PredictionMixin
from nowplaying.orchestrator._heartbeat_handlers import HeartbeatHandlersMixin
from nowplaying.orchestrator._sonos_handlers import SonosHandlersMixin

# Re-export names that tests patch via "nowplaying.orchestrator._class.<name>".
# mock.patch patches the name where it's *used* (i.e. in the mixin modules),
# so these imports alone don't make the patches land — but they keep existing
# test imports of this module working without modification.
# Tests that patch nowplaying.orchestrator._class.history need to patch the
# actual module where history is used. The imports below keep backward compat
# for any test that does `from nowplaying.orchestrator._class import X`.
from nowplaying import history  # noqa: F401 — re-exported for test compat
from nowplaying.orchestrator.fingerprint import _build_fingerprint_payload  # noqa: F401

log = logging.getLogger("nowplaying.main")


class Orchestrator(
    SonosHandlersMixin,
    HeartbeatHandlersMixin,
    PredictionMixin,
    LLMHooksMixin,
    PublishEnrichmentMixin,
):
    """Owns app-runtime state and exposes the event handlers main_async wires up.

    Built once at start; methods are bound to Sonos / capture / route hooks.
    All formerly-captured locals (state, bcast, sonos_coord, stop) become
    attributes set in __init__.
    """

    def __init__(
        self,
        state: "State",
        bcast,
        sonos_coord,
        stop: asyncio.Event,
        llm: LLMAssist,
        fingerprint_enabled: bool = False,
    ) -> None:
        self.state = state
        self.bcast = bcast
        self.sonos_coord = sonos_coord
        self.stop = stop
        self.llm = llm
        self.fingerprint_enabled = fingerprint_enabled

    async def hygiene_loop(self) -> None:
        stop = self.stop

        def _sweep_all() -> None:
            hygiene.sweep_clips()
        try:
            await asyncio.to_thread(_sweep_all)
        except Exception as e:
            log.warning("hygiene startup sweep failed: %r", e)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=3600)
                return
            except asyncio.TimeoutError:
                try:
                    await asyncio.to_thread(_sweep_all)
                except Exception as e:
                    log.warning("hygiene sweep failed: %r", e)
