"""Tests for the Shazam circuit breaker.

We inject a fake monotonic clock so the rolling-window math and the
suppression/recovery transitions can be exercised without sleeping.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `nowplaying` importable when running pytest from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

import pytest

from nowplaying.vinyl.ratelimit import (
    SHAZAM_BACKOFF_INITIAL_S,
    SHAZAM_BACKOFF_MAX_S,
    CircuitBreaker,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_breaker(
    clock: FakeClock,
    *,
    soft: int = 5,
    hard: int = 10,
    hard_window: float = 60.0,
    open_initial: float = 30.0,
    open_max: float = 300.0,
) -> CircuitBreaker:
    return CircuitBreaker(
        name="test",
        soft_cap_per_min=soft,
        hard_cap_per_min=hard,
        hard_cap_window_s=hard_window,
        open_initial_s=open_initial,
        open_max_s=open_max,
        clock=clock,
    )


def _burst(b: CircuitBreaker, clock: FakeClock, n: int, dt: float = 0.1) -> int:
    """Try n calls; return the count that were actually allowed."""
    allowed = 0
    for _ in range(n):
        if b.should_allow():
            b.record_attempt()
            allowed += 1
        clock.advance(dt)
    return allowed


# ---------------------------------------------------------------------- tests


def test_soft_cap_warns_but_allows(caplog):
    clock = FakeClock()
    b = make_breaker(clock, soft=5, hard=100, hard_window=60.0)

    caplog.set_level(logging.WARNING, logger="nowplaying.vinyl.ratelimit")

    # 6 calls in <60s: soft cap is 5 → at the 6th `should_allow` we should
    # warn but still allow. All 6 should be allowed (soft cap is advisory).
    allowed = _burst(b, clock, 6, dt=0.1)
    assert allowed == 6

    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warn_records, "expected a soft-cap WARNING"
    assert "rate elevated" in warn_records[0].getMessage()


def test_hard_cap_trips_and_recovers(caplog):
    clock = FakeClock()
    b = make_breaker(clock, soft=100, hard=10, hard_window=60.0, open_initial=30.0)
    # hard_cap_count = 10 * 60 / 60 = 10

    caplog.set_level(logging.ERROR, logger="nowplaying.vinyl.ratelimit")

    # Fire 10 attempts (under the cap) — all allowed.
    for _ in range(10):
        assert b.should_allow() is True
        b.record_attempt()
        clock.advance(0.1)

    # The 11th should_allow sees 10 attempts in window → trips and returns False.
    assert b.should_allow() is False
    assert b.status()["state"] == "open"

    err = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert err and "circuit OPEN" in err[0].getMessage()

    # Still suppressed shortly after trip.
    clock.advance(5.0)
    assert b.should_allow() is False

    # After both the open window AND the rolling window elapse, the circuit
    # closes and old attempts have aged out so we don't immediately re-trip.
    clock.advance(75.0)
    assert b.should_allow() is True
    assert b.status()["state"] == "closed"


def test_429_immediately_opens_circuit():
    clock = FakeClock()
    b = make_breaker(clock, soft=100, hard=100, hard_window=60.0, open_initial=30.0)

    # Only one attempt — nowhere near any cap.
    assert b.should_allow() is True
    b.record_attempt()
    b.record_failure(rate_limited=True)

    assert b.should_allow() is False
    assert b.status()["state"] == "open"

    # And it eventually recovers.
    clock.advance(31.0)
    assert b.should_allow() is True


def test_consecutive_failures_exponential_backoff():
    clock = FakeClock()
    b = make_breaker(clock, soft=100, hard=100, hard_window=60.0)

    assert b.should_allow() is True
    b.record_attempt()
    b.record_failure()  # first failure → backoff = INITIAL (1s)

    # Within the backoff window: blocked.
    clock.advance(SHAZAM_BACKOFF_INITIAL_S / 2.0)
    assert b.should_allow() is False

    # After the backoff: allowed again.
    clock.advance(SHAZAM_BACKOFF_INITIAL_S)
    assert b.should_allow() is True
    b.record_attempt()
    b.record_failure()
    b.record_failure()  # 3 consecutive → delay = min(2^2, MAX) = 4s

    expected = min(SHAZAM_BACKOFF_INITIAL_S * 4, SHAZAM_BACKOFF_MAX_S)
    # Just before the delay elapses: still blocked.
    clock.advance(expected - 0.5)
    assert b.should_allow() is False
    # Just after: allowed.
    clock.advance(1.0)
    assert b.should_allow() is True


def test_success_resets_backoff():
    clock = FakeClock()
    b = make_breaker(clock, soft=100, hard=100, hard_window=60.0)

    b.record_attempt()
    b.record_failure()
    b.record_failure()
    assert b.consecutive_failures == 2
    assert b.next_allowed > clock.t

    b.record_success()
    assert b.consecutive_failures == 0
    assert b.next_allowed == 0.0
    assert b.should_allow() is True


def test_auto_recovery_logs_closed(caplog):
    clock = FakeClock()
    b = make_breaker(clock, soft=100, hard=100, hard_window=60.0, open_initial=10.0)

    # Force open via 429.
    b.record_attempt()
    b.record_failure(rate_limited=True)
    assert b.should_allow() is False

    caplog.set_level(logging.INFO, logger="nowplaying.vinyl.ratelimit")

    clock.advance(11.0)
    assert b.should_allow() is True

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("circuit closed" in r.getMessage() for r in info)


def test_rolling_window_prunes_old_attempts():
    clock = FakeClock()
    b = make_breaker(clock, soft=100, hard=5, hard_window=60.0, open_initial=30.0)

    # 4 attempts now, then advance well past the rolling window.
    for _ in range(4):
        assert b.should_allow() is True
        b.record_attempt()
        clock.advance(0.1)

    clock.advance(120.0)

    # Those 4 should have aged out — fire another 4, still allowed.
    for _ in range(4):
        assert b.should_allow() is True
        b.record_attempt()
        clock.advance(0.1)

    assert b.status()["state"] == "closed"


def test_status_shape():
    clock = FakeClock()
    b = make_breaker(clock)
    b.record_attempt()
    s = b.status()
    for key in (
        "name",
        "state",
        "calls_in_last_min",
        "calls_in_hard_window",
        "open_until",
        "next_allowed",
        "consecutive_failures",
        "open_count",
        "call_counter",
    ):
        assert key in s
    assert s["state"] == "closed"
    assert s["calls_in_last_min"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
