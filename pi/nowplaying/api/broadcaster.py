"""Broadcaster + WS handler — fans out NowPlaying payloads to kiosk clients."""
from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import WSMsgType, web

from nowplaying.api._paths import log

# Fields that determine whether two consecutive payloads are content-equivalent.
# Timestamps (ts, track_started_at) and transport metadata (anchor_source,
# duration_seconds, queue) are intentionally excluded: they change on every
# Sonos UPnP resubscribe NOTIFY even when nothing the kiosk cares about has
# changed.  Comparing only these fields means a Sonos resubscribe that
# re-asserts an identical {state, source, title=None, ...} is correctly
# identified as redundant and suppressed.
PUBLISH_CONTENT_FIELDS: tuple[str, ...] = (
    "state",
    "source",
    "title",
    "artist",
    "release_id",
    "track_position",
    "match_method",
    "art_url",
    "album",
    "guess",
    "predicted",
)

# match_method values that indicate real track recognition has occurred.
# A payload carrying one of these methods (with actual metadata) should never
# be suppressed — it represents a genuine audio event.
_REAL_RECOGNITION_METHODS: frozenset[str] = frozenset(
    {
        "shazam",
        "fingerprint",
        "fingerprint-anchor",
        "user-identified",
        "user-selected",
        "sonos-didl",
        "sonos-polled",
    }
)


def _is_stopped_to_empty_vinyl(
    prev: dict[str, Any],
    curr: dict[str, Any],
) -> bool:
    """Return True when *curr* is a bare vinyl-PLAYING after idle (STOPPED).

    Sonos reports state=PLAYING for Line-In whenever the source is *selected*,
    regardless of whether audio is actually flowing.  When the orchestrator is
    idle (last publish was state=STOPPED) and a Sonos resubscribe NOTIFY arrives
    with state=PLAYING source=vinyl but no track metadata, it is NOT a real audio
    event — it is Sonos re-asserting source selection after renewing its UPnP
    subscription.  Suppressing this prevents the kiosk from leaving the idle
    clock screen on every ~10-minute Sonos resubscribe cycle.

    Suppression conditions (all must hold):
    - prev state == STOPPED   (we are currently idle)
    - curr state == PLAYING
    - curr source == vinyl
    - curr title is None/absent
    - curr artist is None/absent
    - curr release_id is None/absent
    - curr match_method not in _REAL_RECOGNITION_METHODS (or absent)

    The kiosk leaves idle only when capture detects real audio (audible-edge) or
    a Sonos NOTIFY arrives with actual track metadata (title, artist, etc.).
    """
    if prev.get("state") != "STOPPED":
        return False
    if curr.get("state") != "PLAYING":
        return False
    if curr.get("source") != "vinyl":
        return False
    # Any real track metadata present → this is a genuine audio event.
    if curr.get("title") is not None:
        return False
    if curr.get("artist") is not None:
        return False
    if curr.get("release_id") is not None:
        return False
    if curr.get("match_method") in _REAL_RECOGNITION_METHODS:
        return False
    return True


def _payloads_are_redundant(
    prev: dict[str, Any] | None,
    curr: dict[str, Any],
) -> tuple[bool, str]:
    """Return (True, reason) iff *curr* should be suppressed given *prev*.

    Two suppression paths:
    - ``content-identical``: all PUBLISH_CONTENT_FIELDS match (PR #185 case —
      handles second-and-later Sonos resubscribes that re-assert the same state).
    - ``stopped-to-empty-vinyl``: prev=STOPPED and curr is a bare vinyl-PLAYING
      with no track metadata (handles the FIRST resubscribe NOTIFY after idle).

    Returns False when *prev* is None (first publish always fires).
    """
    if prev is None:
        return False, ""
    if all(prev.get(f) == curr.get(f) for f in PUBLISH_CONTENT_FIELDS):
        return True, "content-identical"
    if _is_stopped_to_empty_vinyl(prev, curr):
        return True, "stopped-to-empty-vinyl"
    return False, ""


class Broadcaster:
    """Fan-out of NowPlaying payloads to connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[web.WebSocketResponse] = set()
        self._last: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def add(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._clients.add(ws)
            last = self._last
        if last is not None:
            await ws.send_json({"type": "now_playing", "payload": last})

    async def remove(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def publish(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            prev = self._last
            redundant, reason = _payloads_are_redundant(prev, payload)
            if redundant:
                log.info(
                    "publish: redundant (skipped) reason=%s source=%s title=%r",
                    reason,
                    payload.get("source"),
                    payload.get("title"),
                )
                return
            # Why: snapshot the payload so later in-place mutations by callers
            # (e.g. pin_track mutates state.last_vinyl, which orchestrator
            # publishes by reference) don't retroactively change our notion
            # of "last published" and make the next publish look redundant.
            self._last = dict(payload)
            clients = list(self._clients)
        log.info(
            "publish: clients=%d source=%s title=%r release_id=%s",
            len(clients),
            payload.get("source"),
            payload.get("title"),
            payload.get("release_id"),
        )
        if not clients:
            return
        msg = {"type": "now_playing", "payload": payload}
        dead: list[web.WebSocketResponse] = []
        for ws in clients:
            if ws.closed:
                dead.append(ws)
                continue
            try:
                await ws.send_json(msg)
            except Exception as e:
                log.warning("publish send failed: %r", e)
                dead.append(ws)
        if dead:
            log.info("dropping %d dead client(s)", len(dead))
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    bcast: Broadcaster = request.app["broadcaster"]
    await bcast.add(ws)
    log.info("ws client connected (%d total)", len(bcast._clients))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                log.warning("ws error: %s", ws.exception())
                break
    finally:
        await bcast.remove(ws)
        log.info("ws client disconnected (%d total)", len(bcast._clients))
    return ws
