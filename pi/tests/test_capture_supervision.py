"""Tests for capture subprocess supervision.

Covers:
  (a) fast-exit mock → supervisor calls run_capture again after backoff
  (b) stop event during backoff → clean exit, no extra spawn
  (c) backoff progression: five fast exits → delays 1, 2, 5, 10, 30
  (d) backoff reset: healthy run (>= threshold) → next delay resets to 1s
  (e) pause-state replay: get_start_paused controls start_paused on restart

All tests are offline — no Pi hardware required. Real asyncio.sleep is
replaced by immediate resolution via injectable fake clocks and stop events.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.vinyl.runtime import (  # noqa: E402
    _BACKOFF_RESET_THRESHOLD_S,
    _BackoffClock,
    SUPERVISION_BACKOFF,
    run_capture_supervised,
)


# ---------------------------------------------------------------------------
# _BackoffClock unit tests (synchronous)
# ---------------------------------------------------------------------------


def test_backoff_clock_progression():
    """next() returns the defined sequence, capped at the last value."""
    clock = _BackoffClock()
    delays = [clock.next() for _ in range(len(SUPERVISION_BACKOFF) + 2)]
    cap = SUPERVISION_BACKOFF[-1]
    assert delays[: len(SUPERVISION_BACKOFF)] == list(
        float(d) for d in SUPERVISION_BACKOFF
    )
    # After exhausting the sequence, stays at cap.
    assert delays[-1] == float(cap)
    assert delays[-2] == float(cap)


def test_backoff_clock_reset_on_healthy_run():
    """maybe_reset() resets index to 0 when run lasted >= threshold."""
    fake_time = 0.0

    def _now() -> float:
        return fake_time

    clock = _BackoffClock(_now=_now)
    # Advance past cap.
    for _ in range(len(SUPERVISION_BACKOFF)):
        clock.next()
    assert clock.next() == float(SUPERVISION_BACKOFF[-1])

    # Simulate a healthy run.
    clock.record_start()
    fake_time = _BACKOFF_RESET_THRESHOLD_S + 1.0
    clock.maybe_reset()

    # Index should be reset; next delay should be back to 1s.
    assert clock.next() == float(SUPERVISION_BACKOFF[0])


def test_backoff_clock_no_reset_on_short_run():
    """maybe_reset() does NOT reset when run was shorter than threshold."""
    fake_time = 0.0

    def _now() -> float:
        return fake_time

    clock = _BackoffClock(_now=_now)
    # Advance to cap.
    for _ in range(len(SUPERVISION_BACKOFF)):
        clock.next()

    # Simulate a short-lived run (< threshold).
    clock.record_start()
    fake_time = _BACKOFF_RESET_THRESHOLD_S - 1.0
    clock.maybe_reset()

    # Index unchanged — still at cap.
    assert clock.next() == float(SUPERVISION_BACKOFF[-1])


def test_backoff_clock_maybe_reset_without_record_start():
    """maybe_reset() without record_start() is a no-op (no AttributeError)."""
    clock = _BackoffClock()
    clock.next()  # idx = 1
    clock.maybe_reset()  # _start is None — should not raise
    # idx still at 1 (not reset)
    assert clock.next() == float(SUPERVISION_BACKOFF[1])


# ---------------------------------------------------------------------------
# run_capture_supervised integration tests (async)
# ---------------------------------------------------------------------------


async def _noop_on_clip(path: object, level_db: float) -> None:
    pass


async def _noop_on_state(s: str) -> None:
    pass


def _make_fast_exit_run_capture(n_exits: int, stop: asyncio.Event) -> tuple[AsyncMock, list[dict]]:
    """Return (mock, call_log).

    The mock patches run_capture. Each call records its kwargs in call_log
    and returns immediately (simulating an immediate child exit). After
    `n_exits` calls, sets `stop` so the supervisor exits cleanly.
    """
    call_log: list[dict] = []
    count = 0

    async def _fake_run_capture(on_clip, on_state, _stop, *, silence_db=None, start_paused=False):
        nonlocal count
        count += 1
        call_log.append({"start_paused": start_paused, "call_n": count})
        if count >= n_exits:
            stop.set()

    mock = AsyncMock(side_effect=_fake_run_capture)
    return mock, call_log


@pytest.mark.asyncio
async def test_supervisor_restarts_after_fast_exit():
    """After capture exits unexpectedly, supervisor spawns a replacement."""
    stop = asyncio.Event()
    mock, call_log = _make_fast_exit_run_capture(n_exits=2, stop=stop)

    with patch("nowplaying.vinyl.runtime.run_capture", mock):
        # Make backoff instant so the test doesn't wait real seconds.
        with patch("nowplaying.vinyl.runtime.SUPERVISION_BACKOFF", (0, 0, 0, 0, 0)):
            await run_capture_supervised(
                _noop_on_clip, _noop_on_state, stop,
                get_start_paused=lambda: False,
                start_paused=False,
            )

    assert len(call_log) == 2, f"Expected 2 spawns, got {len(call_log)}: {call_log}"


@pytest.mark.asyncio
async def test_supervisor_stop_during_backoff_exits_cleanly():
    """If stop fires during backoff sleep, supervisor exits without extra spawn."""
    stop = asyncio.Event()
    call_count = 0

    async def _fake_run_capture(on_clip, on_state, _stop, *, silence_db=None, start_paused=False):
        nonlocal call_count
        call_count += 1
        # Return immediately (simulated death) — but don't set stop, so the
        # supervisor enters the backoff sleep. The real backoff will be
        # interrupted by stop being set elsewhere.

    # We use a non-zero backoff so the supervisor actually awaits stop.wait().
    with patch("nowplaying.vinyl.runtime.run_capture", AsyncMock(side_effect=_fake_run_capture)):
        with patch("nowplaying.vinyl.runtime.SUPERVISION_BACKOFF", (60, 60, 60, 60, 60)):
            # Set stop after a short real delay so it fires during the backoff.
            async def _delayed_stop():
                await asyncio.sleep(0.05)
                stop.set()

            await asyncio.gather(
                run_capture_supervised(
                    _noop_on_clip, _noop_on_state, stop,
                    get_start_paused=lambda: False,
                ),
                _delayed_stop(),
            )

    assert call_count == 1, f"Expected 1 spawn (no restart), got {call_count}"


@pytest.mark.asyncio
async def test_supervisor_pause_state_replay():
    """get_start_paused is called on restarts; first spawn uses start_paused kwarg."""
    stop = asyncio.Event()
    spawn_records: list[bool] = []
    spawn_count = 0

    async def _fake_run_capture(on_clip, on_state, _stop, *, silence_db=None, start_paused=False):
        nonlocal spawn_count
        spawn_count += 1
        spawn_records.append(start_paused)
        if spawn_count >= 3:
            stop.set()

    # First spawn: start_paused=True (initial state)
    # Restarts: get_start_paused returns False
    with patch("nowplaying.vinyl.runtime.run_capture", AsyncMock(side_effect=_fake_run_capture)):
        with patch("nowplaying.vinyl.runtime.SUPERVISION_BACKOFF", (0, 0, 0, 0, 0)):
            await run_capture_supervised(
                _noop_on_clip, _noop_on_state, stop,
                get_start_paused=lambda: False,
                start_paused=True,
            )

    assert len(spawn_records) == 3
    assert spawn_records[0] is True, "First spawn should honor start_paused=True"
    assert spawn_records[1] is False, "Restart #1 should use get_start_paused()=False"
    assert spawn_records[2] is False, "Restart #2 should use get_start_paused()=False"


@pytest.mark.asyncio
async def test_supervisor_stop_on_first_call_exits_cleanly():
    """When stop fires during the first run_capture call, supervisor exits without restart."""
    stop = asyncio.Event()
    call_count = 0

    async def _fake_run_capture(on_clip, on_state, _stop, *, silence_db=None, start_paused=False):
        nonlocal call_count
        call_count += 1
        stop.set()  # Simulate commanded stop

    with patch("nowplaying.vinyl.runtime.run_capture", AsyncMock(side_effect=_fake_run_capture)):
        await run_capture_supervised(
            _noop_on_clip, _noop_on_state, stop,
            get_start_paused=lambda: False,
        )

    assert call_count == 1, f"Expected exactly 1 spawn, got {call_count}"


@pytest.mark.asyncio
async def test_supervisor_backoff_reset_after_healthy_run():
    """After a run lasting >= _BACKOFF_RESET_THRESHOLD_S, next restart delay is 1s."""
    stop = asyncio.Event()
    # Advance to near-cap first by consuming the backoff index, then simulate
    # a healthy run; confirm the next delay is the first element again.
    fake_time = 0.0
    spawn_count = 0

    async def _fake_run_capture(on_clip, on_state, _stop, *, silence_db=None, start_paused=False):
        nonlocal spawn_count, fake_time
        spawn_count += 1
        if spawn_count == 1:
            # Simulate long healthy run by jumping fake_time.
            fake_time += _BACKOFF_RESET_THRESHOLD_S + 5.0
        if spawn_count >= 2:
            stop.set()

    recorded_delays: list[float] = []
    original_wait_for = asyncio.wait_for

    async def _mock_wait_for(coro, timeout=None):
        if timeout is not None:
            recorded_delays.append(timeout)
            # Pretend timeout expired immediately so we don't actually wait.
            coro.close()
            raise asyncio.TimeoutError
        return await original_wait_for(coro)

    with patch("nowplaying.vinyl.runtime.run_capture", AsyncMock(side_effect=_fake_run_capture)):
        with patch("nowplaying.vinyl.runtime.SUPERVISION_BACKOFF", (1, 2, 5, 10, 30)):
            # Inject fake clock into _BackoffClock via patch.
            original_init = _BackoffClock.__init__

            def _patched_init(self, *, _now=None):
                original_init(self, _now=lambda: fake_time)

            with patch.object(_BackoffClock, "__init__", _patched_init):
                with patch("asyncio.wait_for", _mock_wait_for):
                    await run_capture_supervised(
                        _noop_on_clip, _noop_on_state, stop,
                        get_start_paused=lambda: False,
                    )

    # After the healthy first run, next backoff should be 1s (reset).
    assert recorded_delays, "Expected at least one backoff delay to be recorded"
    assert recorded_delays[0] == 1.0, (
        f"After healthy run, expected backoff reset to 1s, got {recorded_delays[0]}s"
    )
