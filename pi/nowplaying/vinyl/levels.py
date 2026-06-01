"""Audio level thresholds for the vinyl capture chain — single source of truth.

These dBFS-RMS thresholds decide "program audio present" vs "silence" across
both the capture daemon (``scripts/capture_proto.py``) and the orchestrator
(music-level routing + dead-air detection). Keeping them here means a hardware
or input-level change is a one-place retune.

Calibrated to the capture chain's program level. Reference measurement
(clean LINE input into the UFO202, 2026-06-01): music ≈ −20 dBFS RMS, idle
noise floor ≈ −41 dBFS RMS. Re-measure and re-anchor here if that changes.

The two thresholds form a hysteresis band ("no man's land"): the capture gate
flips to *silent* only below ``SILENCE_DB`` and to *audible* only above
``MUSIC_DB``. A level hovering near one line can't flap, because flipping back
requires crossing all the way to the other line.
"""
from __future__ import annotations

# Fall below this → silence: the capture gate stops emitting heartbeat clips.
SILENCE_DB = -34.0

# Rise above this → program audio present. Used as the capture gate's audible
# up-cross AND by the orchestrator to classify a heartbeat as music (recognition
# routing in `_is_music_level`, and dead-air detection in the track-guess gate).
MUSIC_DB = -30.0
