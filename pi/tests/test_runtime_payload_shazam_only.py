"""Tests for to_now_playing_vinyl on the Shazam-only (no Discogs)
publish path.

Regression for Bug 1: when ``release_id is None``, the wrapper-extracted
``art_url`` / ``albumadamid`` / ``release_mbid`` were dropped from the
payload. They must now propagate so the kiosk can render the album art
and downstream consumers can use the MBID.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))


def test_shazam_only_payload_carries_art_and_identifiers():
    """No release_id (no Discogs match). The Shazam wrapper supplied
    art_url + albumadamid + release_mbid → all three must land on the
    published payload."""
    from nowplaying.vinyl.runtime import to_now_playing_vinyl
    result = {
        "ts": "2026-05-27T00:00:00",
        "title": "Ten Cent Pistol",
        "artist": "The Black Keys",
        "album": "Brothers (Deluxe Remastered Anniversary Edition)",
        "release_id": None,
        "art_url": "https://is1-ssl.mzstatic.com/image/.../cover.jpg",
        "albumadamid": "123456789",
        "release_mbid": "df642560-e127-44ba-8144-8faa60fe9979",
        "match_method": "shazam",
        "tracklist": None,
    }
    payload = to_now_playing_vinyl(result)
    assert payload["art_url"] == result["art_url"]
    assert payload["albumadamid"] == "123456789"
    assert payload["release_mbid"] == "df642560-e127-44ba-8144-8faa60fe9979"
    assert payload["release_id"] is None
    assert payload["match_method"] == "shazam"


def test_shazam_only_payload_omits_missing_identifiers():
    """Optional identifiers stay off the payload when not in the
    recognize result (payload-convention: omit None, don't emit nulls)."""
    from nowplaying.vinyl.runtime import to_now_playing_vinyl
    payload = to_now_playing_vinyl({
        "ts": "2026-05-27T00:00:00",
        "title": "x",
        "artist": "y",
        "album": "z",
        "release_id": None,
        "match_method": "shazam",
    })
    assert "art_url" not in payload
    assert "release_mbid" not in payload
    assert "albumadamid" not in payload


def test_discogs_hit_path_uses_art_url_for_release(monkeypatch):
    """When release_id is set, the payload's art_url comes from the
    Discogs canonical resolver — NOT from the Shazam wrapper. Discogs
    scans beat Apple's CDN."""
    from nowplaying.vinyl import runtime as runtime_mod
    # Stub the publish-enrichment import path used inside the function.
    from nowplaying.orchestrator import _publish_enrichment
    monkeypatch.setattr(
        _publish_enrichment, "_art_url_for_release",
        lambda rid: f"/art/{rid}.jpg",
    )
    payload = runtime_mod.to_now_playing_vinyl({
        "ts": "2026-05-27T00:00:00",
        "title": "x",
        "artist": "y",
        "album": "z",
        "release_id": 42,
        "art_url": "https://shazam/should-be-overridden.jpg",
        "match_method": "shazam",
    })
    assert payload["art_url"] == "/art/42.jpg"


def test_payload_propagates_release_mbid_on_discogs_path(monkeypatch):
    """release_mbid is independent of release_id — it can be attached
    via the discovered-release path even when Discogs also has a hit."""
    from nowplaying.vinyl import runtime as runtime_mod
    from nowplaying.orchestrator import _publish_enrichment
    monkeypatch.setattr(
        _publish_enrichment, "_art_url_for_release",
        lambda rid: f"/art/{rid}.jpg",
    )
    payload = runtime_mod.to_now_playing_vinyl({
        "ts": "2026-05-27T00:00:00",
        "title": "x",
        "artist": "y",
        "album": "z",
        "release_id": 42,
        "release_mbid": "mb-xyz",
        "match_method": "shazam",
    })
    assert payload["release_mbid"] == "mb-xyz"
