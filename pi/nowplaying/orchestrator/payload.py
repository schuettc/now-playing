"""Sonos-event → kiosk-payload translation helpers."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from urllib.parse import quote as _urlquote


# Map listener source labels → kiosk Source enum.
SOURCE_MAP = {
    "vinyl": "vinyl",
    "airplay": "airplay",
    "tv": "tv",
    "stream": "streaming",
    "radio": "radio",
    "library": "streaming",
    "grouped": "unknown",
    "unknown": "unknown",
    "idle": "unknown",
}


def service_art_url(ev: dict) -> str | None:
    """Proxy Sonos art under a per-track key (see ``artcache.key_for_track``).

    Polled events may already carry a legacy proxy URL; extract its
    original `u` first.
    """
    from nowplaying import artcache

    art_url = ev.get("album_art")
    if not art_url:
        return None
    if art_url.startswith("/art-cache/"):
        art_url = parse_qs(urlsplit(art_url).query).get("u", [None])[0]
    if not art_url:
        return None
    key = artcache.key_for_track(ev.get("artist"), ev.get("album"), ev.get("title"), art_url)
    return f"/art-cache/{key}?u={_urlquote(art_url, safe='')}"


def _cached_art_url(ev: dict) -> str | None:
    return service_art_url(ev)


def _apply_sonos_anchor(payload: dict, ev: dict) -> None:
    """Carry track_started_at / duration / anchor_source from the listener
    onto the payload in place.
    """
    if ev.get("track_started_at"):
        payload["track_started_at"] = ev["track_started_at"]
        payload["anchor_source"] = "sonos"
    if ev.get("duration_seconds"):
        payload["duration_seconds"] = ev["duration_seconds"]


def sonos_to_payload(ev: dict) -> dict:
    src_in = ev.get("source", "unknown")
    src = SOURCE_MAP.get(src_in, "unknown")
    payload: dict = {
        "ts": ev["ts"],
        "state": ev.get("state") or "STOPPED",
        "source": src,
        "title": ev.get("title"),
        "artist": ev.get("artist"),
        "album": ev.get("album"),
        "art_url": service_art_url(ev),
        "match_method": "sonos-polled" if ev.get("sonos_polled") else "sonos-didl",
    }
    _apply_sonos_anchor(payload, ev)
    if src == "vinyl" and not payload["title"]:
        # Will be enriched by the vinyl recognition pipeline.
        payload["match_method"] = "unmatched"
    if src == "airplay":
        payload["device_name"] = "AirPlay device"
    return payload
