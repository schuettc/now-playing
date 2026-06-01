"""Promotion dispatcher tests.

When coverage promotion fires off a fingerprint anchor:
- Anchor with ``release_id`` (Discogs) → ``vinyl.promotion.maybe_promote``.
- Anchor with ``mbid`` (discovered) → ``discovery.fingerprint.add_ref``.

Exercises ``_schedule_coverage_promotion`` directly with a fabricated
Orchestrator + anchor; mirrors ``test_orchestrator_coverage_promotion``'s
fixture shape.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.discovery import fingerprint as discovery_fingerprint
from nowplaying.orchestrator.pin import ANCHOR_TTL_BUFFER_S
from nowplaying.orchestrator.streaming_idle import MUSIC_DB
from nowplaying.vinyl import promotion

_MONO_NOW = 2_000_000.0
_MUSIC_LEVEL = MUSIC_DB + 10.0
_FAKE_WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


def _make_rid_anchor(elapsed_s: float = 5.0) -> dict:
    return {
        "release_id": 42,
        "mbid": None,
        "track_position": "A1",
        "monotonic_ts": _MONO_NOW - elapsed_s,
        "duration_seconds": 240,
        "hits": 50,
        "last_matched_ref_position_s": 30.0,
    }


def _make_mbid_anchor(elapsed_s: float = 5.0) -> dict:
    return {
        "release_id": None,
        "mbid": "harvest-mb-1",
        "track_position": "A1",
        "monotonic_ts": _MONO_NOW - elapsed_s,
        "duration_seconds": 240,
        "hits": 50,
        "last_matched_ref_position_s": 30.0,
    }


@pytest.fixture
def orch(monkeypatch):
    from nowplaying.main import Orchestrator

    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = _MONO_NOW
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    o = Orchestrator.__new__(Orchestrator)
    o.fingerprint_enabled = True
    o.state = mock.MagicMock()
    o.state.user_track_pin = None
    o.state.fingerprint_anchor = None
    o.state.sonos_source = "vinyl"
    return o


@pytest.mark.asyncio
async def test_rid_anchor_routes_to_vinyl_promotion(orch, monkeypatch):
    """Anchor with release_id set → vinyl.promotion.maybe_promote."""
    orch.state.fingerprint_anchor = _make_rid_anchor()
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    rid_promote = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(promotion, "maybe_promote", rid_promote)
    mbid_add = mock.MagicMock()
    monkeypatch.setattr(discovery_fingerprint, "add_ref", mbid_add)

    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    assert create_task_mock.called, "expected asyncio.create_task for rid promotion"
    mbid_add.assert_not_called()


@pytest.mark.asyncio
async def test_mbid_anchor_routes_to_discovery_fingerprint(orch, monkeypatch):
    """Anchor with mbid set (no rid) → discovery.fingerprint.add_ref."""
    orch.state.fingerprint_anchor = _make_mbid_anchor()
    # should_promote_for_coverage must NOT be consulted on the discovered path
    # (cohort gates are release_id-keyed). Sentinel: blow up if called.
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage",
        mock.MagicMock(side_effect=AssertionError(
            "should_promote_for_coverage must not run on MBID-keyed promotion"
        )),
    )
    rid_promote = mock.AsyncMock()
    monkeypatch.setattr(promotion, "maybe_promote", rid_promote)
    mbid_add = mock.MagicMock(return_value=1)
    monkeypatch.setattr(discovery_fingerprint, "add_ref", mbid_add)

    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    assert create_task_mock.called, "expected asyncio.create_task for discovered promotion"
    rid_promote.assert_not_called()


@pytest.mark.asyncio
async def test_empty_anchor_skips_promotion(orch, monkeypatch):
    """Anchor with neither release_id nor mbid → no promotion."""
    bad_anchor = _make_rid_anchor()
    bad_anchor["release_id"] = None
    bad_anchor["mbid"] = None
    orch.state.fingerprint_anchor = bad_anchor
    monkeypatch.setattr(
        promotion, "should_promote_for_coverage", lambda *a, **kw: True,
    )
    rid_promote = mock.AsyncMock()
    monkeypatch.setattr(promotion, "maybe_promote", rid_promote)
    mbid_add = mock.MagicMock()
    monkeypatch.setattr(discovery_fingerprint, "add_ref", mbid_add)

    with mock.patch("asyncio.create_task") as create_task_mock:
        await orch._schedule_coverage_promotion(_FAKE_WAV, _MUSIC_LEVEL)

    create_task_mock.assert_not_called()
    rid_promote.assert_not_called()
    mbid_add.assert_not_called()


# Used to keep ANCHOR_TTL_BUFFER_S import live for future tests; touching it
# here also confirms the symbol resolves (module-load smoke).
_ = ANCHOR_TTL_BUFFER_S
