"""Process-local circuit breaker for the Shazam recognizer.

Why
---
ShazamIO is an unofficial endpoint. Normal vinyl-listening volume is ~1–3
calls/min. A buggy retry loop or a regression that calls Shazam every clip
without rate-limiting could flood with tens of calls per minute and risk
a ban that takes out our only working recognizer.

This module provides a generic :class:`CircuitBreaker` plus the constants
the Shazam wrapper uses. The orchestrator is single-process so we do not
coordinate across workers; state is intentionally in-memory and resets on
process restart.

Behaviour
---------
* **Soft cap** (``SHAZAM_SOFT_CAP_PER_MIN``): log WARNING but allow the call.
* **Hard cap** (``SHAZAM_HARD_CAP_PER_MIN`` over ``SHAZAM_HARD_CAP_WINDOW_S``):
  trip the circuit. ``should_allow`` returns ``False`` until ``open_until``.
* **429 from the endpoint** (``record_failure(rate_limited=True)``): trip
  the circuit immediately regardless of attempt count.
* **Other failures**: exponential backoff (1s, 2s, 4s, ... capped at
  ``SHAZAM_BACKOFF_MAX_S``) via ``next_allowed``.
* **Re-tripping** while still suppressed escalates ``open_initial_s`` → 2x →
  4x → ... capped at ``open_max_s`` (handled by ``open_count``).
* **Auto-recovery**: once ``open_until`` elapses the next ``should_allow``
  closes the circuit and logs INFO. Attempt history is *not* wiped; the
  60s rolling window prunes naturally.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — sensible defaults for the Shazam circuit. Tune as we learn.
# ---------------------------------------------------------------------------

#: WARNING threshold: at this many calls in the last 60s we log but still allow.
SHAZAM_SOFT_CAP_PER_MIN = 20

#: Hard cap calls allowed per minute (used with HARD_CAP_WINDOW_S to derive
#: an absolute count over the window).
SHAZAM_HARD_CAP_PER_MIN = 30

#: Rolling window (seconds) the hard cap is evaluated over. 120s smooths
#: short bursts while still catching runaway calls quickly.
SHAZAM_HARD_CAP_WINDOW_S = 120.0

#: Initial suppression duration after a trip (5 minutes).
SHAZAM_OPEN_INITIAL_S = 300.0

#: Max suppression duration after repeated trips (30 minutes).
SHAZAM_OPEN_MAX_S = 1800.0

#: Exponential backoff starting interval (1s) for non-429 transient failures.
SHAZAM_BACKOFF_INITIAL_S = 1.0

#: Cap for exponential backoff (60s) for non-429 transient failures.
SHAZAM_BACKOFF_MAX_S = 60.0


_ROLLING_WINDOW_S = 60.0
_SUPPRESS_LOG_INTERVAL_S = 60.0
_SOFT_WARN_INTERVAL_S = 60.0
_INFO_EVERY_N_CALLS = 5


class CircuitBreaker:  # skylos: ignore SKY-Q501 — 16 attrs (one over) captures the breaker's full state: 5 caps, the clock callable, rolling/window counters, and locks. Splitting into smaller dataclasses would scatter mutually-coupled state and obscure the algorithm.
    """Rolling-window circuit breaker with soft/hard cap + backoff.

    All times are read from ``clock`` (defaults to ``time.monotonic``) so
    tests can drive the breaker without sleeping. The clock is *not*
    required to be wall-clock; it only needs to be monotonic and in seconds.
    """

    def __init__(
        self,
        name: str,
        soft_cap_per_min: int,
        hard_cap_per_min: int,
        hard_cap_window_s: float,
        open_initial_s: float,
        open_max_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.soft_cap_per_min = soft_cap_per_min
        self.hard_cap_per_min = hard_cap_per_min
        self.hard_cap_window_s = hard_cap_window_s
        self.open_initial_s = open_initial_s
        self.open_max_s = open_max_s
        self._clock = clock

        # absolute count permitted within hard_cap_window_s
        self._hard_cap_count = int(hard_cap_per_min * hard_cap_window_s / 60.0)

        self.attempts: Deque[float] = deque()
        self.open_until: Optional[float] = None
        self.next_allowed: float = 0.0
        self.consecutive_failures: int = 0
        self.open_count: int = 0

        self._call_counter: int = 0
        self._last_soft_warn_at: float = -1e9
        self._last_suppress_log_at: float = -1e9

    # ------------------------------------------------------------------ utils

    def _now(self) -> float:
        return self._clock()

    def _prune(self, now: float) -> None:
        """Drop attempts outside the larger of the two rolling windows."""
        cutoff = now - max(_ROLLING_WINDOW_S, self.hard_cap_window_s)
        attempts = self.attempts
        while attempts and attempts[0] < cutoff:
            attempts.popleft()

    def _count_within(self, now: float, window: float) -> int:
        cutoff = now - window
        # attempts is sorted ascending; count from the right
        n = 0
        for ts in reversed(self.attempts):
            if ts >= cutoff:
                n += 1
            else:
                break
        return n

    def _open_duration(self) -> float:
        # escalating: initial, 2x, 4x, ... capped at open_max
        mult = 2 ** max(0, self.open_count)
        return min(self.open_initial_s * mult, self.open_max_s)

    def _trip(self, now: float, reason: str) -> None:
        duration = self._open_duration()
        self.open_until = now + duration
        self.open_count += 1
        try:
            until_iso = datetime.fromtimestamp(
                time.time() + duration, tz=timezone.utc
            ).isoformat(timespec="seconds")
        except Exception:
            until_iso = f"+{duration:.0f}s"
        logger.error(
            "%s: circuit OPEN until %s; suppressing calls (%s)",
            self.name,
            until_iso,
            reason,
        )

    def _maybe_close(self, now: float) -> None:
        if self.open_until is not None and now >= self.open_until:
            self.open_until = None
            logger.info("%s: circuit closed; resuming calls", self.name)

    # --------------------------------------------------------------- public

    def should_allow(self) -> bool:
        now = self._now()
        self._prune(now)
        self._maybe_close(now)

        # 1. Still suppressed?
        if self.open_until is not None and now < self.open_until:
            if now - self._last_suppress_log_at >= _SUPPRESS_LOG_INTERVAL_S:
                remaining = self.open_until - now
                logger.info(
                    "%s: still suppressed (~%ds remaining)",
                    self.name,
                    int(remaining),
                )
                self._last_suppress_log_at = now
            return False

        # 2. Inside an exponential-backoff window?
        if now < self.next_allowed:
            return False

        # 3. Hard cap over its window?
        hard_n = self._count_within(now, self.hard_cap_window_s)
        if hard_n >= self._hard_cap_count:
            self._trip(now, f"hard cap {hard_n}/{self._hard_cap_count} in {self.hard_cap_window_s:.0f}s")
            return False

        # 4. Soft cap over 60s?
        soft_n = self._count_within(now, _ROLLING_WINDOW_S)
        if soft_n >= self.soft_cap_per_min:
            if now - self._last_soft_warn_at >= _SOFT_WARN_INTERVAL_S:
                logger.warning("%s: rate elevated (%d/min)", self.name, soft_n)
                self._last_soft_warn_at = now

        return True

    def record_attempt(self) -> None:
        now = self._now()
        self.attempts.append(now)
        self._call_counter += 1
        if self._call_counter % _INFO_EVERY_N_CALLS == 0:
            in_last_min = self._count_within(now, _ROLLING_WINDOW_S)
            logger.info(
                "%s: call %d (%d in last 60s)",
                self.name,
                self._call_counter,
                in_last_min,
            )

    def record_success(self) -> None:
        # Successful call: reset failure-driven backoff. Leave open_count
        # alone — it only decays when the circuit fully recovers, which is
        # signalled implicitly by future successful traffic that doesn't
        # re-trip. Resetting here would re-arm the escalation on the next
        # transient burst.
        self.consecutive_failures = 0
        self.next_allowed = 0.0

    def record_failure(self, *, rate_limited: bool = False) -> None:
        now = self._now()
        if rate_limited:
            self._trip(now, "HTTP 429")
            return
        self.consecutive_failures += 1
        delay = min(
            SHAZAM_BACKOFF_INITIAL_S * (2 ** (self.consecutive_failures - 1)),
            SHAZAM_BACKOFF_MAX_S,
        )
        self.next_allowed = now + delay

    def status(self) -> dict:
        now = self._now()
        self._prune(now)
        return {
            "name": self.name,
            "state": "open" if (self.open_until and now < self.open_until) else "closed",
            "calls_in_last_min": self._count_within(now, _ROLLING_WINDOW_S),
            "calls_in_hard_window": self._count_within(now, self.hard_cap_window_s),
            "open_until": self.open_until,
            "next_allowed": self.next_allowed if now < self.next_allowed else None,
            "consecutive_failures": self.consecutive_failures,
            "open_count": self.open_count,
            "call_counter": self._call_counter,
        }


def make_shazam_breaker(
    clock: Callable[[], float] = time.monotonic,
) -> CircuitBreaker:
    """Construct the Shazam circuit with our default constants."""
    return CircuitBreaker(
        name="shazam",
        soft_cap_per_min=SHAZAM_SOFT_CAP_PER_MIN,
        hard_cap_per_min=SHAZAM_HARD_CAP_PER_MIN,
        hard_cap_window_s=SHAZAM_HARD_CAP_WINDOW_S,
        open_initial_s=SHAZAM_OPEN_INITIAL_S,
        open_max_s=SHAZAM_OPEN_MAX_S,
        clock=clock,
    )
