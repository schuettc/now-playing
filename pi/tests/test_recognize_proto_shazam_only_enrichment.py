"""Tests for `recognize_proto.recognize`'s shazam-only branch enrichment.

When Discogs returns no match, the cascade now attaches Shazam's `album`,
`art_url`, and `albumadamid` to the publish payload so the kiosk can render
more than artist + title. When Discogs matches, these are NOT overwritten —
Discogs is canonical for those fields. See feature `shazam-enrichment-plumbing`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

# Import the script as a module. Path is `pi/scripts/recognize_proto.py`.
_SCRIPTS = _PI_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import recognize_proto  # noqa: E402


_SHAZAM_FULL = {
    "title": "Heart of Gold",
    "subtitle": "Neil Young",
    "artist": "Neil Young",
    "isrc": "USRE17500095",
    "album": "Harvest",
    "art_url": "https://is2-ssl.mzstatic.com/image/coverarthq.jpg",
    "albumadamid": "203708420",
    "raw": {},
    "match_offset_s": 12.5,
    "match_timeskew": 0.0,
}


def _run(clip_path: Path) -> dict:
    return asyncio.run(recognize_proto.recognize(clip_path))


def test_shazam_only_branch_attaches_new_fields() -> None:
    """Discogs returns None → kiosk gets album + art_url + albumadamid."""
    with mock.patch.object(recognize_proto.shazam, "identify", new=mock.AsyncMock(return_value=_SHAZAM_FULL)), \
         mock.patch.object(recognize_proto.discogs_catalog, "find_by_artist_title", return_value=None):
        out = _run(Path("/tmp/fake.wav"))
    assert out["artist"] == "Neil Young"
    assert out["title"] == "Heart of Gold"
    assert out["album"] == "Harvest"
    assert out["art_url"] == "https://is2-ssl.mzstatic.com/image/coverarthq.jpg"
    assert out["albumadamid"] == "203708420"
    # `match_method` stays "shazam" — this is the shazam-only branch.
    assert out["match_method"] == "shazam"


def test_discogs_hit_does_not_overwrite_with_shazam_art() -> None:
    """When Discogs matches, art_url stays unset; Discogs's art_path wins."""
    rel = {
        "id": 42,
        "artist": "Neil Young",
        "title": "Harvest (1972 Reprise pressing)",
        "year": 1972,
        "label": "Reprise",
        "catno": "MS 2032",
        "art_path": "/var/lib/nowplaying/art/42.jpg",
        "tracks": [],
        "matched_track_title": "Heart of Gold",
        "matched_track_position": "A3",
        "match_score": 0.95,
    }
    with mock.patch.object(recognize_proto.shazam, "identify", new=mock.AsyncMock(return_value=_SHAZAM_FULL)), \
         mock.patch.object(recognize_proto.discogs_catalog, "find_by_artist_title", return_value=rel), \
         mock.patch.object(recognize_proto.asyncio, "create_task"):
        out = _run(Path("/tmp/fake.wav"))
    # Discogs's pressing-specific title wins.
    assert out["album"] == "Harvest (1972 Reprise pressing)"
    # And we do NOT smuggle the Shazam art_url onto a Discogs-hit payload.
    assert "art_url" not in out
    assert "albumadamid" not in out
    assert out["art_path"] == "/var/lib/nowplaying/art/42.jpg"


def test_shazam_only_branch_omits_keys_when_shazam_did_not_supply_them() -> None:
    """No album / art / id from Shazam → no spurious keys on the payload."""
    sparse = dict(_SHAZAM_FULL)
    sparse["album"] = None
    sparse["art_url"] = None
    sparse["albumadamid"] = None
    with mock.patch.object(recognize_proto.shazam, "identify", new=mock.AsyncMock(return_value=sparse)), \
         mock.patch.object(recognize_proto.discogs_catalog, "find_by_artist_title", return_value=None):
        out = _run(Path("/tmp/fake.wav"))
    # `base` initializes album to None; we only overwrite when truthy.
    assert out["album"] is None
    assert "art_url" not in out
    assert "albumadamid" not in out
