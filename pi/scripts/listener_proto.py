"""Phase 1 Sonos listener prototype.

Subscribes to AVTransport events on the configured zone coordinator and prints
a unified now-playing dict per event. Also appends each event as JSONL to
`pi/data/phase1_capture.jsonl` for later replay/analysis.

Run:
    uv run python pi/scripts/listener_proto.py

Env (loaded from pi/.env if present):
    SONOS_ZONE_NAME    Zone to follow (default: "Office")
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

from soco import SoCo, config as soco_config, discover, events_asyncio

soco_config.EVENTS_MODULE = events_asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
PI_DIR = REPO_ROOT / "pi"
DATA_DIR = PI_DIR / "data"
CAPTURE_PATH = DATA_DIR / "phase1_capture.jsonl"

load_dotenv(PI_DIR / ".env")
ZONE_NAME = os.environ.get("SONOS_ZONE_NAME", "Office")

URI_SOURCE_MAP: list[tuple[str, str]] = [
    ("x-rincon-stream:", "vinyl"),
    ("x-sonos-vli:", "airplay"),
    ("x-sonos-htastream:", "tv"),
    ("x-sonos-http:", "stream"),
    ("x-sonos-spotify:", "stream"),
    ("x-sonosapi-stream:", "radio"),
    ("x-sonosapi-hls:", "radio"),
    ("x-rincon-mp3radio:", "radio"),
    ("x-file-cifs:", "library"),
    ("x-rincon:", "grouped"),
]

DIDL_NS = {
    "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
    "r": "urn:schemas-rinconnetworks-com:metadata-1-0/",
}


def classify_uri(uri: str | None) -> tuple[str, str | None]:
    if not uri:
        return ("idle", None)
    for prefix, source in URI_SOURCE_MAP:
        if uri.startswith(prefix):
            return (source, prefix)
    return ("unknown", None)


def parse_didl(didl_xml: str | None) -> dict[str, str | None]:
    out: dict[str, str | None] = {"title": None, "artist": None, "album": None, "album_art": None}
    if not didl_xml or didl_xml in ("NOT_IMPLEMENTED", ""):
        return out
    try:
        root = ET.fromstring(didl_xml)
    except ET.ParseError:
        return out
    item = root.find("didl:item", DIDL_NS)
    if item is None:
        return out
    title_el = item.find("dc:title", DIDL_NS)
    artist_el = item.find("dc:creator", DIDL_NS)
    if artist_el is None:
        artist_el = item.find("upnp:artist", DIDL_NS)
    album_el = item.find("upnp:album", DIDL_NS)
    art_el = item.find("upnp:albumArtURI", DIDL_NS)
    out["title"] = title_el.text if title_el is not None else None
    out["artist"] = artist_el.text if artist_el is not None else None
    out["album"] = album_el.text if album_el is not None else None
    out["album_art"] = art_el.text if art_el is not None else None
    return out


def find_zone(name: str) -> SoCo | None:
    zones = discover(timeout=5) or set()
    for z in zones:
        if z.player_name == name:
            return z
    return None


def build_event(coordinator: SoCo, variables: dict) -> dict:
    state = variables.get("transport_state") or "UNKNOWN"
    uri = variables.get("current_track_uri") or variables.get("enqueued_transport_uri") or None
    source, prefix = classify_uri(uri)

    didl_raw = (
        variables.get("current_track_meta_data")
        or variables.get("enqueued_transport_uri_meta_data")
    )
    didl = parse_didl(_didl_to_str(didl_raw))

    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "zone": coordinator.player_name,
        "state": state,
        "source": source,
        "title": didl["title"],
        "artist": didl["artist"],
        "album": didl["album"],
        "album_art": didl["album_art"],
        "track_number": variables.get("current_track"),
        "duration": variables.get("current_track_duration"),
        "uri": uri,
        "raw_uri_prefix": prefix,
    }


def _didl_to_str(value) -> str | None:
    """SoCo sometimes hands back a DidlObject; normalize to raw XML string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    to_didl = getattr(value, "to_didl_string", None)
    if callable(to_didl):
        return to_didl()
    return str(value)


async def main_async() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    capture = CAPTURE_PATH.open("a", buffering=1)

    print(f"[listener] discovering zone {ZONE_NAME!r}...", flush=True)
    coord = await asyncio.get_running_loop().run_in_executor(None, find_zone, ZONE_NAME)
    if coord is None:
        raise SystemExit(f"zone {ZONE_NAME!r} not found via SSDP discovery")

    print(f"[listener] subscribing on {coord.player_name} ({coord.ip_address})", flush=True)

    def on_avtransport(event):
        try:
            payload = build_event(coord, event.variables)
            line = json.dumps(payload, ensure_ascii=False)
            print(line, flush=True)
            capture.write(line + "\n")
        except Exception as e:
            print(f"[listener] error handling event: {e!r}", flush=True)

    def on_topology(event):
        # Light-touch — note group changes; full re-resolution is out of scope for proto.
        zgs = event.variables.get("zone_group_state")
        print(f"[topology] event seq={event.seq} (zone_group_state present={bool(zgs)})", flush=True)

    av_sub = await coord.avTransport.subscribe()
    av_sub.callback = on_avtransport
    topo_sub = await coord.zoneGroupTopology.subscribe()
    topo_sub.callback = on_topology

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        for action in (
            ("unsubscribe AVTransport", av_sub.unsubscribe()),
            ("unsubscribe ZoneGroupTopology", topo_sub.unsubscribe()),
            ("stop event listener", events_asyncio.event_listener.async_stop()),
        ):
            label, coro = action
            try:
                await coro
            except Exception as e:
                print(f"[listener] non-fatal cleanup error during {label}: {e!r}", flush=True)
        capture.close()
        print("[listener] shut down cleanly", flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
