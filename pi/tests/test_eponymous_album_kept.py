"""The disambiguated album name ("American Football (2019)") survives a
fingerprint hit and an LLM release swap, as it does a Shazam hit."""
from __future__ import annotations

from nowplaying.discogs import catalog
from nowplaying.orchestrator import fingerprint as orch_fp
from nowplaying.orchestrator.llm import _reverse_lookup
from nowplaying.vinyl.fingerprint import Hit

_LP3 = {
    "id": 3,
    "artist": "American Football",
    "title": "American Football",
    "disambiguated_album": "American Football (2019)",
    "year": 2019,
    "tracks": [{"position": "A1", "side": "A", "title": "Silhouettes", "duration_seconds": 444}],
}


def test_blind_fingerprint_payload_keeps_the_disambiguated_album(monkeypatch):
    monkeypatch.setattr(catalog, "get_release", lambda rid: dict(_LP3))
    top = Hit(ref_id=1, release_id=3, track_position="A1", hits=100, track_position_s=0.0)
    payload = orch_fp._build_blind_fingerprint_payload(top, "vinyl")
    assert payload["album"] == "American Football (2019)"


def test_llm_release_swap_keeps_the_disambiguated_album():
    result: dict = {}
    _reverse_lookup._swap_result_fields(
        result, dict(_LP3), {"release_id": 3, "track_position": "A1", "track_title": "Silhouettes"},
    )
    assert result["album"] == "American Football (2019)"
