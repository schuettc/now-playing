"""Streaming and AirPlay art comes from Sonos, not the Discogs collection.

When Apple Music (or any Sonos-native stream) plays a song that happens
to be on a record in the user's Discogs collection, the service already
told us exactly which track and which cover it is. The Discogs match is
still worth having for release_id / tracklist / album metadata, but its
vinyl scan must not replace the service's own art.

Precedence, top to bottom:
  1. an explicit user art override for (artist, album)
  2. the Sonos-supplied art (proxied through /art-cache/...)
  3. /art/<release_id>, only when Sonos supplied no art at all
"""
from __future__ import annotations

from unittest.mock import patch

from nowplaying.art_overrides import Override
from nowplaying.orchestrator._publish_enrichment import PublishEnrichmentMixin


class _Enricher(PublishEnrichmentMixin):
    """Bare mixin host — these helpers touch no orchestrator state."""


SONOS_ART = "/art-cache/abc123?u=http%3A%2F%2F192.168.4.5%3A1400%2Fgetaa%3Fx"

RELEASE = {
    "id": 3112846,
    "title": "Plans",
    "year": 2005,
    "label": "Atlantic",
    "catno": "83605-1",
    "matched_track_position": "B2",
    "tracks": [
        {"position": "B2", "title": "Your Heart Is an Empty Room",
         "duration_seconds": 224},
        {"position": "B3", "title": "Someday You Will Be Loved",
         "duration_seconds": 212},
    ],
}


def _payload(**over: object) -> dict:
    base = {
        "source": "airplay",
        "artist": "Death Cab for Cutie",
        "title": "Your Heart Is an Empty Room",
        "album": "Plans",
        "art_url": SONOS_ART,
    }
    base.update(over)
    return base


def _override(epoch: int = 1779478774) -> Override:
    return Override(
        key="abc",
        url="https://example/picked.jpg",
        source="discogs-master",
        picked_at="2026-05-22T19:39:34Z",
        local_path="/tmp/picked.jpg",
        content_type="image/jpeg",
        picked_at_epoch=epoch,
    )


def test_discogs_match_keeps_the_sonos_art() -> None:
    out = _Enricher()._apply_discogs_release_to_payload(_payload(), RELEASE)
    assert out["art_url"] == SONOS_ART


def test_discogs_match_still_enriches_metadata() -> None:
    """Art is the only thing the Discogs match stops patching."""
    out = _Enricher()._apply_discogs_release_to_payload(_payload(), RELEASE)
    assert out["release_id"] == 3112846
    assert out["track_position"] == "B2"
    assert out["side"] == "B"
    assert out["year"] == 2005
    assert [t["position"] for t in out["tracklist"]] == ["B2", "B3"]


def test_falls_back_to_release_art_when_sonos_supplied_none() -> None:
    """Empty art is worse than approximate art."""
    with patch(
        "nowplaying.orchestrator._publish_enrichment._art_url_for_release",
        return_value="/art/3112846",
    ):
        out = _Enricher()._apply_discogs_release_to_payload(
            _payload(art_url=None), RELEASE,
        )
    assert out["art_url"] == "/art/3112846"


def test_user_override_wins_over_sonos_art_on_a_matched_stream() -> None:
    """The override rewrite used to skip any payload carrying a
    release_id, on the assumption that /art/<rid> resolved overrides
    itself. Streaming art no longer routes through /art/<rid>, so the
    rewrite has to run for matched streams too or a deliberate pick is
    silently ignored."""
    matched = _payload(release_id=3112846, art_url=SONOS_ART)
    with patch(
        "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
        return_value=_override(),
    ):
        out = _Enricher()._rewrite_art_url_for_overrides(matched)
    assert out["art_url"] == (
        "/art-by-name?artist=Death+Cab+for+Cutie&album=Plans&v=1779478774"
    )


def test_vinyl_payload_art_is_untouched_by_the_override_rewrite() -> None:
    """Regression guard: vinyl keeps resolving overrides through
    /art/<rid> and must not be rerouted to /art-by-name."""
    vinyl = _payload(source="vinyl", release_id=3112846, art_url="/art/3112846")
    with patch(
        "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
        return_value=_override(),
    ):
        out = _Enricher()._rewrite_art_url_for_overrides(vinyl)
    assert out["art_url"] == "/art/3112846"
