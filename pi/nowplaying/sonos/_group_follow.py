"""Follow the group's lead speaker while the watched zone is a group member.

A zone that has joined another room's group reports only "following
<leader>" (`x-rincon:<uid>`) on its own AVTransport: what is playing lives
on the leader. While that lasts, the leader's AVTransport is subscribed too
and its events are published as the zone's. The zone's own subscription
stays, because it is what reports the zone joining and leaving a group.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from nowplaying.sonos._listener_supervisor import _SonosListener

log = logging.getLogger("nowplaying.sonos.listener")


class GroupFollower:
    """Sits between the zone's events and the orchestrator's handler."""

    def __init__(
        self, zone, on_event, loop: asyncio.AbstractEventLoop, *,
        on_coord: Callable | None, build_initial_payload: Callable,
    ) -> None:
        self.zone = zone
        self._on_event = on_event
        self._loop = loop
        self._on_coord = on_coord
        self._build_initial_payload = build_initial_payload
        self._leader = None
        self._leader_sub = None
        self._lock = asyncio.Lock()

    def adopt_zone(self, zone) -> None:
        """The zone was re-discovered (for example at a new address)."""
        self.zone = zone
        if self._leader is None and self._on_coord is not None:
            self._on_coord(zone)

    async def on_zone_event(self, payload: dict) -> None:
        """An event from the watched zone itself."""
        async with self._lock:
            if payload.get("source") != "grouped":
                await self._stop_following()
                await self._forward(payload)
                return
            leader = await self._resolve_leader()
            if leader is not None and not self._is_following(leader):
                await self._stop_following(restore_coord=False)
                await self._start_following(leader)
            if self._leader is None:
                await self._forward(payload)
                return
            await self._forward_leader_state()

    async def close(self) -> None:
        async with self._lock:
            await self._stop_following(restore_coord=False)

    def _is_following(self, leader) -> bool:
        return self._leader is not None and self._leader.uid == leader.uid

    async def _resolve_leader(self):
        def _lookup():
            group = self.zone.group
            return group.coordinator if group is not None else None
        try:
            leader = await self._loop.run_in_executor(None, _lookup)
        except Exception as exc:  # noqa: BLE001 — Why: soco raises several types on a topology read; any of them means "leader unknown"
            log.warning("[sonos] group leader lookup failed: %r", exc)
            return None
        if leader is None or leader.uid == self.zone.uid:
            return None
        return leader

    async def _start_following(self, leader) -> None:
        listener = _SonosListener(
            leader, lambda payload: self._on_leader_event(leader, payload), self._loop,
        )
        try:
            sub = await leader.avTransport.subscribe(auto_renew=True)
        except Exception as exc:  # noqa: BLE001 — Why: an unreachable leader must fall back to the zone's own event, whatever soco raised
            log.warning(
                "[sonos] could not subscribe to group leader %s: %r",
                leader.player_name, exc,
            )
            return
        sub.callback = listener.on_avtransport
        self._leader, self._leader_sub = leader, sub
        log.info(
            "[sonos] %s is in a group led by %s (%s): following the leader",
            self.zone.player_name, leader.player_name, leader.ip_address,
        )
        if self._on_coord is not None:
            self._on_coord(leader)

    async def _stop_following(self, *, restore_coord: bool = True) -> None:
        if self._leader is None:
            return
        leader, sub = self._leader, self._leader_sub
        self._leader = self._leader_sub = None
        try:
            await sub.unsubscribe()
        except Exception as exc:  # noqa: BLE001 — Why: cleanup of a subscription that may already be gone
            log.debug("[sonos] leader unsubscribe error (non-fatal): %r", exc)
        log.info("[sonos] no longer following group leader %s", leader.player_name)
        if restore_coord and self._on_coord is not None:
            self._on_coord(self.zone)

    async def _on_leader_event(self, leader, payload: dict) -> None:
        if not self._is_following(leader):
            return
        await self._forward(payload)

    async def _forward_leader_state(self) -> None:
        leader = self._leader
        try:
            info = await self._loop.run_in_executor(None, leader.get_current_track_info)
            ti = await self._loop.run_in_executor(None, leader.get_current_transport_info)
        except Exception as exc:  # noqa: BLE001 — Why: a failed poll leaves the last published state; the leader's next event corrects it
            log.warning("[sonos] group leader poll failed: %r", exc)
            return
        await self._forward(self._build_initial_payload(leader, info or {}, ti or {}))

    async def _forward(self, payload: dict) -> None:
        result = self._on_event(payload)
        if asyncio.iscoroutine(result):
            await result
