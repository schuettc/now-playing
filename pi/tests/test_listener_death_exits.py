"""When the Sonos listener task dies, the service must exit non-zero so
systemd (Restart=on-failure) starts it again.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.orchestrator import bootstrap


async def _until(stop: asyncio.Event) -> None:
    await stop.wait()


def _patched_main(run_listener):
    """main_async with everything but the shutdown wiring stubbed out."""
    orch = mock.MagicMock()
    orch.on_sonos_event = mock.AsyncMock()
    orch.sonos_repoll_loop = mock.AsyncMock()
    orch.hygiene_loop = mock.AsyncMock()
    runner = mock.MagicMock()
    runner.cleanup = mock.AsyncMock()

    async def capture(_hb, _cs, stop, **_kw):
        await stop.wait()

    patches = [
        mock.patch.object(bootstrap, "load_dotenv"),
        mock.patch.object(
            bootstrap, "_init_optional_features",
            new=mock.AsyncMock(return_value=(mock.MagicMock(), False)),
        ),
        mock.patch.object(bootstrap.art_overrides, "prewarm"),
        mock.patch.object(
            bootstrap, "_build_app",
            new=mock.AsyncMock(
                return_value=(mock.MagicMock(), runner, mock.MagicMock()),
            ),
        ),
        mock.patch.object(bootstrap, "find_zone", return_value=mock.MagicMock()),
        mock.patch.object(bootstrap, "emit_initial_state", new=mock.AsyncMock()),
        mock.patch.object(bootstrap, "Orchestrator", return_value=orch),
        mock.patch.object(bootstrap, "run_capture_supervised", new=capture),
        mock.patch.object(bootstrap, "run_listener", new=run_listener),
    ]
    return patches, runner


def _run_main(run_listener):
    patches, runner = _patched_main(run_listener)
    for p in patches:
        p.start()
    try:
        asyncio.run(asyncio.wait_for(bootstrap.main_async(), timeout=5))
    finally:
        for p in patches:
            p.stop()
    return runner


def test_listener_death_exits_non_zero_after_cleanup():
    async def dying_listener(_zone, _on_event, _stop, **_kw):
        raise RuntimeError("zone 'Office' not found during re-discovery")

    patches, runner = _patched_main(dying_listener)
    for p in patches:
        p.start()
    try:
        with pytest.raises(SystemExit) as exc:
            asyncio.run(asyncio.wait_for(bootstrap.main_async(), timeout=5))
    finally:
        for p in patches:
            p.stop()
    assert exc.value.code == 1
    runner.cleanup.assert_awaited_once()


def test_requested_stop_exits_cleanly():
    async def listener(_zone, _on_event, stop, **_kw):
        asyncio.get_running_loop().call_later(0.05, stop.set)
        await stop.wait()

    runner = _run_main(listener)
    runner.cleanup.assert_awaited_once()
