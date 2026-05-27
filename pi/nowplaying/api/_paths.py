"""Shared filesystem anchors for the HTTP layer."""
from __future__ import annotations

import logging
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PI_DIR = REPO_ROOT / "pi"
KIOSK_DIST = REPO_ROOT / "kiosk" / "dist"
ART_DIR = PI_DIR / "data" / "art"
MUSICBRAINZ_ART_DIR = ART_DIR / "musicbrainz"

log = logging.getLogger("nowplaying.api")
