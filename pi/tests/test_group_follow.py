"""The watched zone follows its group's lead speaker.

From 2026-11-21 19:05: "Bedroom 2" led a group playing the turntable's
line-in and "Office", the watched zone, was a member. Office's own source
was `x-rincon:<Bedroom 2>`, published as an unknown source with no artist
or album, so the record was never identified.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nowplaying.sonos._group_follow import GroupFollower
from nowplaying.sonos import listener as _listener
from nowplaying.sonos.listener import _build_initial_payload

LINE_IN = "x-rincon-stream:RINCON_347E5CD2C09A01400:0"


def _speaker(name: str, ip: str, uid: str, uri: str) -> tuple[MagicMock, MagicMock]:
    sp = MagicMock()
    sp.player_name, sp.ip_address, sp.uid, sp.port = name, ip, uid, 1400
    sp.get_current_track_info = MagicMock(return_value={"uri": uri, "title": ""})
    sp.get_current_transport_info = MagicMock(
        return_value={"current_transport_state": "PLAYING"},
    )
    sub = MagicMock()
    sub.sid, sub.timeout = f"sid-{name}", 1800
    sub.unsubscribe = AsyncMock()
    sp.avTransport.subscribe = AsyncMock(return_value=sub)
    return sp, sub


def _office_in_bedroom_group() -> tuple[MagicMock, MagicMock, MagicMock]:
    leader, leader_sub = _speaker(
        "Bedroom 2", "192.168.4.136", "RINCON_74CA60A877D401400", LINE_IN,
    )
    office, _ = _speaker(
        "Office", "192.168.5.213", "RINCON_C43875F0B27E01400",
        "x-rincon:RINCON_74CA60A877D401400",
    )
    office.group.coordinator = leader
    return office, leader, leader_sub


def _grouped_event(office: MagicMock) -> dict:
    return _build_initial_payload(
        office, office.get_current_track_info(), {"current_transport_state": "PLAYING"},
    )


def _follower(office: MagicMock) -> tuple[GroupFollower, list[dict], list]:
    events: list[dict] = []
    coords: list = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    follower = GroupFollower(
        office, on_event, asyncio.get_running_loop(),
        on_coord=coords.append, build_initial_payload=_build_initial_payload,
    )
    return follower, events, coords


@pytest.mark.asyncio
async def test_a_group_playing_the_turntable_is_published_as_vinyl():
    office, leader, _ = _office_in_bedroom_group()
    follower, events, coords = _follower(office)

    await follower.on_zone_event(_grouped_event(office))

    assert [e["source"] for e in events] == ["vinyl"]
    assert events[0]["state"] == "PLAYING"
    leader.avTransport.subscribe.assert_awaited_once()
    assert coords == [leader]


@pytest.mark.asyncio
async def test_the_leaders_own_events_are_published_while_following():
    office, leader, leader_sub = _office_in_bedroom_group()
    follower, events, _ = _follower(office)
    await follower.on_zone_event(_grouped_event(office))
    events.clear()

    event = MagicMock()
    event.variables = {
        "transport_state": "PAUSED_PLAYBACK", "current_track_uri": LINE_IN,
    }
    leader_sub.callback(event)
    await asyncio.sleep(0.05)

    assert [(e["source"], e["state"]) for e in events] == [("vinyl", "PAUSED_PLAYBACK")]


@pytest.mark.asyncio
async def test_leaving_the_group_stops_following():
    office, leader, leader_sub = _office_in_bedroom_group()
    follower, events, coords = _follower(office)
    await follower.on_zone_event(_grouped_event(office))
    events.clear()

    alone = dict(_grouped_event(office), source="airplay", uri="x-sonos-vli:x", title="Knee Socks")
    await follower.on_zone_event(alone)

    leader_sub.unsubscribe.assert_awaited_once()
    assert events == [alone]
    assert coords == [leader, office]

    late = MagicMock()
    late.variables = {"transport_state": "PLAYING", "current_track_uri": LINE_IN}
    leader_sub.callback(late)
    await asyncio.sleep(0.05)
    assert events == [alone]


@pytest.mark.asyncio
async def test_a_repeated_grouped_event_does_not_subscribe_again():
    office, leader, _ = _office_in_bedroom_group()
    follower, events, _ = _follower(office)

    await follower.on_zone_event(_grouped_event(office))
    await follower.on_zone_event(_grouped_event(office))

    leader.avTransport.subscribe.assert_awaited_once()
    assert [e["source"] for e in events] == ["vinyl", "vinyl"]


@pytest.mark.asyncio
async def test_a_new_leader_replaces_the_old_one():
    office, leader, leader_sub = _office_in_bedroom_group()
    follower, events, coords = _follower(office)
    await follower.on_zone_event(_grouped_event(office))

    kitchen, _ = _speaker("Kitchen", "192.168.4.118", "RINCON_KITCHEN", "x-sonos-vli:y")
    office.group.coordinator = kitchen
    await follower.on_zone_event(_grouped_event(office))

    leader_sub.unsubscribe.assert_awaited_once()
    kitchen.avTransport.subscribe.assert_awaited_once()
    assert events[-1]["source"] == "airplay"
    assert coords[-1] is kitchen


@pytest.mark.asyncio
async def test_an_unreachable_leader_publishes_the_zones_own_event():
    office, leader, _ = _office_in_bedroom_group()
    leader.avTransport.subscribe = AsyncMock(side_effect=OSError("unreachable"))
    follower, events, _ = _follower(office)

    grouped = _grouped_event(office)
    await follower.on_zone_event(grouped)

    assert events == [grouped]


@pytest.mark.asyncio
async def test_the_startup_probe_reports_the_leaders_state_for_a_grouped_zone():
    office, _, _ = _office_in_bedroom_group()
    events: list[dict] = []

    with patch.object(_listener, "find_zone", return_value=office):
        await _listener.emit_initial_state("Office", events.append)

    assert [(e["source"], e["state"]) for e in events] == [("vinyl", "PLAYING")]
