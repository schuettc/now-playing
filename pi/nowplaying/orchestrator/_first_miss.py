"""First-miss-after-match boundary tracking.

Stamps `state.last_unmatched_after_match_unix_ts` with the unix
wall-clock timestamp of the FIRST unmatched heartbeat following the
most recent Shazam-confirmed match. The pin endpoint consumes this to
backdate `initial_track_position_s` when a pin lands after predicted-
advance (which fires only after ~30s of unmatched heartbeats).

See docs/features/pin-position-ignores-predicted-advance-latency/.
"""
from __future__ import annotations

import time

from nowplaying.orchestrator.state import State


def mark_first_miss_after_match(state: State) -> None:
    """Stamp the first-miss timestamp when a Shazam confirm preceded the
    current miss. No-op if no prior confirm, or if already stamped (we
    want the FIRST miss, not the latest)."""
    if state.last_shazam_match_unix_ts is None:
        return
    if state.last_unmatched_after_match_unix_ts is not None:
        return
    state.last_unmatched_after_match_unix_ts = int(time.time())


def clear_first_miss_after_match(state: State) -> None:
    """Clear the first-miss timestamp. Called everywhere
    last_shazam_match_unix_ts is cleared (idle, source-flip,
    album-lock-change, needs-id) so the lifecycle stays paired."""
    state.last_unmatched_after_match_unix_ts = None
