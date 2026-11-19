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


def test_sonos_art_is_cached_per_track_not_per_session() -> None:
    """Sonos reuses one getaa URL for a whole AirPlay session and serves
    whatever is playing at fetch time, so the URL cannot key the cache."""
    from nowplaying.orchestrator.payload import service_art_url

    ev = {"artist": "The Beatles", "album": "The Beatles", "title": "Wild Honey Pie",
          "album_art": "http://sonos:1400/getaa?v=0&vli=1&u=1149692896"}
    same = service_art_url(dict(ev))
    next_track = service_art_url({**ev, "title": "The Continuing Story of Bungalow Bill"})
    assert service_art_url(ev).split("?")[0] == same.split("?")[0]
    assert service_art_url(ev).split("?")[0] != next_track.split("?")[0]
    assert "u=http%3A%2F%2Fsonos%3A1400%2Fgetaa%3Fv%3D0%26vli%3D1%26u%3D1149692896" in same


def test_legacy_proxied_sonos_url_is_unwrapped() -> None:
    from nowplaying.orchestrator.payload import service_art_url

    ev = {"artist": "American Football", "album": "American Football", "title": "Uncomfortably Numb",
          "album_art": "/art-cache/oldnamekey?u=http%3A%2F%2Fsonos%3A1400%2Fgetaa%3Fu%3Dlp2"}
    raw = {**ev, "album_art": "http://sonos:1400/getaa?u=lp2"}
    assert service_art_url(ev) == service_art_url(raw)


def test_enrichment_scopes_the_discogs_lookup_to_the_reported_album() -> None:
    with patch(
        "nowplaying.orchestrator._publish_enrichment.discogs_catalog.find_by_artist_title",
        return_value=None,
    ) as lookup:
        out = _Enricher()._enrich_sonos_with_discogs(_payload())
    assert lookup.call_args.kwargs["album"] == "Plans"
    assert out["album"] == "Plans"
    assert "release_id" not in out


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


def test_matched_stream_ignores_old_name_pick_and_uses_release_pick() -> None:
    """LP2 must not inherit a name-only pick made for another self-titled LP."""
    matched = _payload(
        artist="American Football", album="American Football",
        release_id=9191767, art_url=SONOS_ART,
    )
    with patch(
        "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
        side_effect=lambda artist, album, *, release_id=None: (
            _override() if release_id is None else None
        ),
    ):
        out = _Enricher()._rewrite_art_url_for_overrides(matched)
    assert out["art_url"] == SONOS_ART


def test_user_override_wins_over_sonos_art_on_a_matched_stream() -> None:
    """A deliberate pick for the matched release beats its service image."""
    matched = _payload(release_id=3112846, art_url=SONOS_ART)
    with patch(
        "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
        return_value=_override(),
    ):
        out = _Enricher()._rewrite_art_url_for_overrides(matched)
    assert out["art_url"] == "/art/3112846?v=1779478774"


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
