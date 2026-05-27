"""Periodic cleanup of raw heartbeat clips in pi/data/clips/.

Capture writes a WAV every heartbeat (~15s) for the recognizer. Old
clips serve no purpose once recognition has run and the result has
been published. We keep them around for 24h after the heartbeat
for debugging recent recognition failures, then delete.

(The candidate_rips/ + promotion pipeline that previously branched
this sweep into a 7-day "candidate" retention class was removed in
2026-05-14's audfprint-zombie-cleanup. Only the orphan retention
path remains.)

Idempotent. Safe to interrupt. Designed to be called hourly from main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("nowplaying.hygiene")

REPO_ROOT = Path(__file__).resolve().parents[3]
PI_DIR = REPO_ROOT / "pi"
DATA_DIR = PI_DIR / "data"
CLIPS_DIR = DATA_DIR / "clips"

ORPHAN_RETENTION_SECONDS = 24 * 3600


def sweep_clips(now: datetime | None = None) -> dict:
    """Walk clips/ and delete files older than ORPHAN_RETENTION_SECONDS.

    Returns counts of {kept, deleted_orphan}.
    """
    now_ts = (now or datetime.now()).timestamp()
    counts = {"kept": 0, "deleted_orphan": 0}
    if not CLIPS_DIR.exists():
        return counts
    for clip in CLIPS_DIR.iterdir():
        if not clip.is_file():
            continue
        try:
            mtime = clip.stat().st_mtime
        except OSError as e:
            log.debug("hygiene: stat failed on %s (likely vanished mid-scan): %r", clip, e)
            continue
        age = now_ts - mtime
        if age >= ORPHAN_RETENTION_SECONDS:
            try:
                clip.unlink()
                counts["deleted_orphan"] += 1
            except OSError as e:
                log.warning("hygiene: failed to delete orphan %s: %r", clip, e)
        else:
            counts["kept"] += 1
    if counts["deleted_orphan"]:
        log.info("hygiene sweep: %s", counts)
    return counts
