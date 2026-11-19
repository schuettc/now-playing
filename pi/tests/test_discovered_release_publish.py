"""A fingerprint hit or a predicted advance on a discovered (MBID-only)
release must build a publishable payload from the discovered store."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
for _p in (_PI_ROOT, _PI_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from nowplaying import catalog as catalog_dispatch  # noqa: E402 — needs the sys.path insert above
from nowplaying.orchestrator.fingerprint import (  # noqa: E402 — same
    _build_blind_fingerprint_payload,
)
from nowplaying.orchestrator.prediction import (  # noqa: E402 — same
    _advance_predicted_position,
    _build_predicted_payload,
)
from nowplaying.vinyl.fingerprint import Hit  # noqa: E402 — same

_MBID = "mb-plans-1"
_DISCOVERED = {
    "mbid": _MBID,
    "artist": "Death Cab for Cutie",
    "title": "Plans",
    "year": 2005,
    "art_url": None,
    "tracks": [
        {"position": "A1", "side": "A", "title": "Marching Bands of Manhattan",
         "duration_seconds": 252, "clean_title": None},
        {"position": "A2", "side": "A", "title": "Soul Meets Body",
         "duration_seconds": 191, "clean_title": None},
    ],
}
_LAST_VINYL = {
    "source": "vinyl", "release_id": None, "release_mbid": _MBID,
    "artist": "Death Cab for Cutie", "album": "Plans", "year": 2005,
    "art_url": "https://example.test/plans.jpg",
    "track_position": "A1", "side": "A",
}


def _discovered_only(release_id=None, mbid=None):
    return _DISCOVERED if release_id is None and mbid == _MBID else None


def test_predicted_advance_on_a_discovered_release_is_publishable():
    current = {
        "release_id": None, "release_mbid": _MBID, "side": "A",
        "track_position": "A1", "index_in_side": 0,
    }
    advanced = _advance_predicted_position(_DISCOVERED["tracks"], current)
    assert advanced is not None
    assert advanced["release_mbid"] == _MBID
    with patch.object(catalog_dispatch, "get_release", _discovered_only):
        payload = _build_predicted_payload(_LAST_VINYL, advanced, "vinyl")
    assert payload is not None
    assert payload["title"] == "Soul Meets Body"
    assert payload["track_position"] == "A2"
    assert payload["release_id"] is None
    assert payload["release_mbid"] == _MBID


def test_blind_fingerprint_hit_on_a_discovered_release_is_publishable():
    top = Hit(
        ref_id=1, release_id=None, track_position="A2", hits=200,
        track_position_s=30.0, mbid=_MBID,
    )
    with patch.object(catalog_dispatch, "get_release", _discovered_only):
        payload = _build_blind_fingerprint_payload(top, "vinyl")
    assert payload is not None
    assert payload["release_id"] is None
    assert payload["release_mbid"] == _MBID
    assert payload["artist"] == "Death Cab for Cutie"
    assert payload["album"] == "Plans"
    assert payload["title"] == "Soul Meets Body"
    assert payload["duration_seconds"] == 191
    assert "art_url" not in payload
    assert [t["position"] for t in payload["tracklist"]] == ["A1", "A2"]
