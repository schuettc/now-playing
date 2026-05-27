"""Tests for the Shazam wrapper's enrichment extraction.

Covers the album / art_url / albumadamid fields added by feature
`shazam-enrichment-plumbing`. The wrapper must:

  - extract album from `track.sections[].metadata` entry titled "Album"
  - prefer `images.coverarthq` over `images.coverart`
  - read `albumadamid` straight off the track
  - gracefully return None for any missing field (real Shazam payloads vary)
"""
from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.vinyl.shazam import _extract_album, _extract_art_url  # noqa: E402


def _golden_track() -> dict:
    """Realistic ShazamIO `track` dict for a vinyl recognition."""
    return {
        "title": "Heart of Gold",
        "subtitle": "Neil Young",
        "isrc": "USRE17500095",
        "albumadamid": "203708420",
        "images": {
            "background": "https://is1-ssl.mzstatic.com/image/bg.jpg",
            "coverart": "https://is2-ssl.mzstatic.com/image/coverart.jpg",
            "coverarthq": "https://is2-ssl.mzstatic.com/image/coverarthq.jpg",
        },
        "sections": [
            {
                "type": "SONG",
                "metadata": [
                    {"title": "Album", "text": "Harvest"},
                    {"title": "Label", "text": "Reprise Records"},
                    {"title": "Released", "text": "1972"},
                ],
            },
            {"type": "VIDEO"},
        ],
    }


def test_extract_album_present() -> None:
    assert _extract_album(_golden_track()) == "Harvest"


def test_extract_album_missing_section() -> None:
    track = _golden_track()
    track["sections"] = [{"type": "SONG", "metadata": [{"title": "Label", "text": "X"}]}]
    assert _extract_album(track) is None


def test_extract_album_no_sections_key() -> None:
    assert _extract_album({}) is None


def test_extract_art_url_prefers_hq() -> None:
    assert _extract_art_url(_golden_track()) == "https://is2-ssl.mzstatic.com/image/coverarthq.jpg"


def test_extract_art_url_falls_back_to_coverart() -> None:
    track = _golden_track()
    track["images"] = {"coverart": "https://is2-ssl.mzstatic.com/image/coverart.jpg"}
    assert _extract_art_url(track) == "https://is2-ssl.mzstatic.com/image/coverart.jpg"


def test_extract_art_url_no_images_block() -> None:
    assert _extract_art_url({}) is None


def test_extract_art_url_empty_images() -> None:
    assert _extract_art_url({"images": {}}) is None


# --- combined-field coverage on a single golden track ---


def test_golden_track_all_three_fields() -> None:
    track = _golden_track()
    assert _extract_album(track) == "Harvest"
    assert _extract_art_url(track) == "https://is2-ssl.mzstatic.com/image/coverarthq.jpg"
    assert track.get("albumadamid") == "203708420"


def test_missing_albumadamid_is_none() -> None:
    track = _golden_track()
    del track["albumadamid"]
    # The wrapper uses `track.get("albumadamid")` — missing key yields None.
    assert track.get("albumadamid") is None
    # And the other two are unaffected.
    assert _extract_album(track) == "Harvest"
    assert _extract_art_url(track) is not None
