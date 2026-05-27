"""Now Playing orchestrator entry point.

Wires:
  - Sonos UPnP listener  → state/source events (streaming/radio/AirPlay/...)
  - UFO202 capture loop  → vinyl audio clips on attack
  - Recognition cascade  → ShazamIO → Discogs disambiguation
  → kiosk WebSocket broadcaster

Run on the Pi:
    cd ~/now-playing/pi
    .venv/bin/python -m nowplaying.main

The actual orchestrator implementation lives in :mod:`nowplaying.orchestrator`
— this file stays as a thin shim so:

  1. ``python -m nowplaying.main`` (the systemd unit's ``ExecStart``)
     keeps launching the orchestrator unchanged.
  2. Tests and any external consumer can keep importing names from
     ``nowplaying.main`` (we re-export the whole public surface below).

Split out 2026-05-16 (SKY-Q502, Phase 3 of the skylos quality epic).
Re-split 2026-05-16 (B-2 restore) accounting for the confirm-first
guess flow added after the original split (D-0 through D-4).
"""
from __future__ import annotations

import asyncio

from nowplaying.orchestrator import (
    DISMISSED_GUESS_TTL_S,
    HEARTBEAT_INTERVAL_S,
    MAX_UNMATCHED_STREAK,
    NEEDS_ID_STREAK,
    Orchestrator,
    MIN_PIN_TTL_S,
    PIN_DIFFERENT_TRACK_RELEASE_STREAK,
    PIN_SAFETY_MARGIN_S,
    PIN_TTL_BUFFER_S,
    PI_DIR,
    RECOGNITION_LEAD_S,
    REPO_ROOT,
    SHAZAM_ONLY_MIN_LEVEL_DB,
    SOURCE_MAP,
    STREAMING_IDLE_DELAY_S,
    State,
    VINYL_IDLE_DELAY_S,
    _advance_predicted_position,
    _apply_sonos_anchor,
    _build_app,
    _build_fingerprint_payload,
    _build_predicted_payload,
    _cached_art_url,
    _compute_advance_elapsed_s,
    _evaluate_sticky_idle,
    _evaluate_user_pin,
    _fingerprint_anchor_ttl_expired,
    _guess_is_dismissed,
    _init_optional_features,
    _interpret_advance_verdict,
    _is_music_level,
    _pin_ttl_expired,
    _read_bytes,
    compute_pin_duration,
    _shazam_disagrees_with_lock,
    _should_arm_streaming_idle,
    _should_cancel_streaming_idle_on_resume,
    _should_pause_capture,
    discogs_catalog,
    main_async,
    sonos_to_payload,
)

__all__ = [
    "DISMISSED_GUESS_TTL_S",
    "HEARTBEAT_INTERVAL_S",
    "MAX_UNMATCHED_STREAK",
    "NEEDS_ID_STREAK",
    "Orchestrator",
    "MIN_PIN_TTL_S",
    "PIN_DIFFERENT_TRACK_RELEASE_STREAK",
    "PIN_SAFETY_MARGIN_S",
    "PIN_TTL_BUFFER_S",
    "PI_DIR",
    "RECOGNITION_LEAD_S",
    "REPO_ROOT",
    "SHAZAM_ONLY_MIN_LEVEL_DB",
    "SOURCE_MAP",
    "STREAMING_IDLE_DELAY_S",
    "State",
    "VINYL_IDLE_DELAY_S",
    "_advance_predicted_position",
    "_apply_sonos_anchor",
    "_build_app",
    "_build_fingerprint_payload",
    "_build_predicted_payload",
    "_cached_art_url",
    "_compute_advance_elapsed_s",
    "_evaluate_sticky_idle",
    "_evaluate_user_pin",
    "_fingerprint_anchor_ttl_expired",
    "_guess_is_dismissed",
    "_init_optional_features",
    "_interpret_advance_verdict",
    "_is_music_level",
    "_pin_ttl_expired",
    "_read_bytes",
    "compute_pin_duration",
    "_shazam_disagrees_with_lock",
    "_should_arm_streaming_idle",
    "_should_cancel_streaming_idle_on_resume",
    "_should_pause_capture",
    "discogs_catalog",
    "main",
    "main_async",
    "sonos_to_payload",
]


def main() -> None:
    """Entry point invoked by systemd via ``python -m nowplaying.main``."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
