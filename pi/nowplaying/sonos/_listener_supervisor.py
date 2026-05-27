"""AVTransport subscription lifecycle supervisor for one Sonos zone.

Extracted from listener.py to keep that module under 500 LOC.
Contains the internal event-building stack and per-zone subscription manager.
Callers (run_listener in listener.py) inject the build_initial_payload callable
so this module has no import dependency on listener.py.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time_module
from datetime import datetime, timedelta, timezone
from typing import Callable
from xml.etree import ElementTree as ET

from soco import SoCo
from soco.exceptions import SoCoException

log = logging.getLogger("nowplaying.sonos.listener")

# ---------------------------------------------------------------------------
# URI → source type mapping
# ---------------------------------------------------------------------------
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

_DIDL_XPATHS: dict[str, tuple[str, str | None]] = {
    "title": ("dc:title", None),
    "artist": ("dc:creator", "upnp:artist"),
    "album": ("upnp:album", None),
    "album_art": ("upnp:albumArtURI", None),
}

_DIDL_EMPTY: dict[str, str | None] = dict.fromkeys(_DIDL_XPATHS)

# ---------------------------------------------------------------------------
# Polled metadata cache TTLs
# ---------------------------------------------------------------------------
_POLL_CACHE_POSITIVE_TTL_S = 1.0
_POLL_CACHE_NEGATIVE_TTL_S = 60.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def classify_uri(uri: str | None) -> tuple[str, str | None]:
    if not uri:
        return ("idle", None)
    for prefix, source in URI_SOURCE_MAP:
        if uri.startswith(prefix):
            return (source, prefix)
    return ("unknown", None)


def _didl_root_item(didl_xml: str | None) -> ET.Element | None:
    if not didl_xml or didl_xml in ("NOT_IMPLEMENTED", ""):
        return None
    try:
        root = ET.fromstring(didl_xml)
    except ET.ParseError as e:
        log.debug("sonos DIDL parse error: %r", e)
        return None
    return root.find("didl:item", DIDL_NS)


def parse_didl(didl_xml: str | None) -> dict[str, str | None]:
    item = _didl_root_item(didl_xml)
    if item is None:
        return dict(_DIDL_EMPTY)
    parsed: dict[str, str | None] = dict(_DIDL_EMPTY)
    for key, (primary, fallback) in _DIDL_XPATHS.items():
        el = item.find(primary, DIDL_NS)
        if el is None and fallback:
            el = item.find(fallback, DIDL_NS)
        parsed[key] = el.text if el is not None else None
    return parsed


def _didl_to_str(value) -> str | None:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    to_didl = getattr(value, "to_didl_string", None)
    return to_didl() if callable(to_didl) else str(value)


def build_event(coordinator: SoCo, variables: dict) -> dict:
    state = variables.get("transport_state") or "UNKNOWN"
    uri = variables.get("current_track_uri") or variables.get("enqueued_transport_uri") or None
    source, prefix = classify_uri(uri)

    didl_raw = (
        variables.get("current_track_meta_data")
        or variables.get("enqueued_transport_uri_meta_data")
    )
    didl = parse_didl(_didl_to_str(didl_raw))

    art = didl["album_art"]
    if art and art.startswith("/"):
        art = f"http://{coordinator.ip_address}:1400{art}"

    return {
        "ts": now_iso(),
        "zone": coordinator.player_name,
        "coordinator_ip": coordinator.ip_address,
        "state": state,
        "source": source,
        "title": didl["title"],
        "artist": didl["artist"],
        "album": didl["album"],
        "album_art": art,
        "track_number": variables.get("current_track"),
        "duration": variables.get("current_track_duration"),
        "uri": uri,
        "raw_uri_prefix": prefix,
        "didl_was_empty": not (didl["title"] or didl["artist"]),
    }


def _parse_hms_to_seconds(s: str | None) -> float | None:
    """Parse Sonos H:MM:SS / M:SS / 'NOT_IMPLEMENTED' position into seconds."""
    if not s or s in ("NOT_IMPLEMENTED",):
        return None
    parts = s.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as e:
        log.debug("sonos HMS parse error for %r: %r", s, e)
        return None
    if len(nums) == 3:
        h, m, sec = nums
        return h * 3600 + m * 60 + sec
    if len(nums) == 2:
        m, sec = nums
        return m * 60 + sec
    if len(nums) == 1:
        return nums[0]
    return None


def _clean_sonos_str(value) -> str | None:
    """Normalize Sonos sentinel values ("", "NOT_IMPLEMENTED") to None."""
    if not value or value == "NOT_IMPLEMENTED":
        return None
    return value


def _poll_current_track(coordinator: SoCo) -> dict | None:
    """Synchronous SOAP call; designed to run in a thread-pool executor."""
    try:
        info = coordinator.get_current_track_info() or {}
    except (SoCoException, OSError) as e:
        log.debug("sonos get_current_track_info failed: %r", e)
        return None
    title = _clean_sonos_str(info.get("title"))
    artist = _clean_sonos_str(info.get("artist"))
    album = _clean_sonos_str(info.get("album"))
    art = info.get("album_art") or None
    position_s = _parse_hms_to_seconds(info.get("position"))
    duration_s = _parse_hms_to_seconds(info.get("duration"))
    if not (title or artist):
        return None
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "album_art": art,
        "position_s": position_s,
        "duration_s": duration_s,
    }


def _poll_queue_sync(coordinator: SoCo, limit: int = 16) -> list[dict]:
    """Fetch the next ``limit`` items from the Sonos zone's playback queue."""
    try:
        items = coordinator.get_queue(0, limit) or []
    except (SoCoException, OSError) as e:
        log.debug("sonos poll_queue failed: %r", e)
        return []
    out: list[dict] = []
    for item in items:
        title = getattr(item, "title", None)
        creator = getattr(item, "creator", None)
        album = getattr(item, "album", None)
        if not title and not creator:
            continue
        out.append({"title": title, "artist": creator, "album": album})
    return out


class _SonosListener:
    """Holds per-zone subscription state shared by the AVTransport callback
    and the async enrichment task.
    """

    def __init__(self, coord, on_event, loop: asyncio.AbstractEventLoop) -> None:
        self.coord = coord
        self.on_event = on_event
        self.loop = loop
        self.sonos_port = getattr(coord, "port", None) or 1400
        self.event_seq = 0
        self.polled_cache: dict[str, tuple[dict | None, float]] = {}
        self.last_event_time: float | None = None

    def _resolve_art(self, art: str | None) -> str | None:
        if art and art.startswith("/"):
            return f"http://{self.coord.ip_address}:{self.sonos_port}{art}"
        return art

    async def enrich_and_republish(self, payload: dict, my_seq: int) -> None:
        uri = payload.get("uri") or ""
        status, cached = self._lookup_cache(uri)
        if status == "skip":
            return
        if cached is None:
            cached = await self._poll_and_cache(uri)
        if not cached:
            return
        if my_seq != self.event_seq:
            log.debug("[sonos] enrich dropped (my_seq=%s event_seq=%s)", my_seq, self.event_seq)
            return
        enriched = self._build_enriched_payload(payload, cached)
        result = self.on_event(enriched)
        if asyncio.iscoroutine(result):
            await result

    def _lookup_cache(self, uri: str) -> tuple[str, dict | None]:
        entry = self.polled_cache.get(uri)
        if entry is None:
            return "miss", None
        value, stamp = entry
        age = self.loop.time() - stamp
        if value is None and age < _POLL_CACHE_NEGATIVE_TTL_S:
            log.debug("[sonos] enrich skip (negative-cache age=%.1fs) uri=%s", age, uri[:80])
            return "skip", None
        if value is not None and age < _POLL_CACHE_POSITIVE_TTL_S:
            return "hit", value
        return "miss", None

    async def _poll_and_cache(self, uri: str) -> dict | None:
        poll_wallclock = datetime.now(timezone.utc)
        try:
            cached = await self.loop.run_in_executor(None, _poll_current_track, self.coord)
        except Exception as e:
            log.warning("[sonos] enrich poll exception: %r", e)
            cached = None
        if cached is not None:
            cached["_poll_wallclock"] = poll_wallclock
        if uri:
            self.polled_cache[uri] = (cached, self.loop.time())
        log.debug(
            "[sonos] enrich poll uri=%s -> %s",
            uri[:80],
            ("hit:" + str(cached.get("title"))) if cached else "miss",
        )
        return cached

    def _build_enriched_payload(self, payload: dict, cached: dict) -> dict:
        from nowplaying import artcache
        from urllib.parse import quote as _urlquote
        enriched = dict(payload)
        enriched["title"] = cached["title"]
        enriched["artist"] = cached["artist"]
        enriched["album"] = cached["album"]
        original_art = self._resolve_art(cached.get("album_art"))
        key = artcache.key_for(cached.get("artist"), cached.get("album"))
        if key and original_art:
            enriched["album_art"] = f"/art-cache/{key}?u={_urlquote(original_art, safe='')}"
        else:
            enriched["album_art"] = original_art
        enriched["didl_was_empty"] = False
        enriched["sonos_polled"] = True
        pos = cached.get("position_s")
        if pos is not None and pos >= 0:
            ref = cached.get("_poll_wallclock") or datetime.now(timezone.utc)
            anchor = ref - timedelta(seconds=pos)
            enriched["track_started_at"] = anchor.isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
        if cached.get("duration_s") is not None:
            enriched["duration_seconds"] = int(cached["duration_s"])
        return enriched

    def on_avtransport(self, event) -> None:
        self.last_event_time = _time_module.monotonic()
        try:
            payload = build_event(self.coord, event.variables)
        except Exception as e:
            log.warning("[sonos] event parse error: %r", e)
            return
        self.event_seq += 1
        my_seq = self.event_seq
        result = self.on_event(payload)
        if asyncio.iscoroutine(result):
            asyncio.run_coroutine_threadsafe(result, self.loop)
        partial = not payload.get("title") or not payload.get("artist")
        if payload.get("source") not in ("vinyl", "unknown") and partial:
            asyncio.run_coroutine_threadsafe(
                self.enrich_and_republish(payload, my_seq), self.loop,
            )


class _ListenerSupervisor:
    """Manages the AVTransport subscription lifecycle for one Sonos zone."""

    def __init__(  # skylos: ignore SKY-C303 — Why: constants injected from listener.py to preserve patch.object test pattern; collapsing them would force tests to reach into the module
        self,
        coord,
        zone_name: str,
        on_event,
        listener: _SonosListener,
        loop: asyncio.AbstractEventLoop,
        stop: asyncio.Event,
        *,
        dead_timeout: float,
        watchdog_interval: float,
        startup_timeout: float,
        max_resub_failures: int,
        find_zone_fn: Callable,
        build_initial_payload_fn: Callable,
    ) -> None:
        self.coord = coord
        self.zone_name = zone_name
        self.on_event = on_event
        self.listener = listener
        self.loop = loop
        self.stop = stop
        self._dead_timeout = dead_timeout
        self._watchdog_interval = watchdog_interval
        self._startup_timeout = startup_timeout
        self._max_resub_failures = max_resub_failures
        self._find_zone = find_zone_fn
        self._build_initial_payload = build_initial_payload_fn
        self.sub_box: list = [None]
        self._renewal_failed = asyncio.Event()

    async def subscribe_once(self) -> None:
        sub = await self.coord.avTransport.subscribe(auto_renew=True)
        sub.callback = self.listener.on_avtransport
        sub.auto_renew_fail = self.on_renew_fail
        self.sub_box[0] = sub
        log.info("[sonos] subscribed (sid=%s timeout=%ss)", sub.sid, sub.timeout)

    def on_renew_fail(self, exc: Exception) -> None:
        log.error("[sonos] auto-renewal failed: %r — watchdog will resubscribe", exc)
        self.loop.call_soon_threadsafe(self._renewal_failed.set)

    async def probe_liveness(self) -> bool:
        try:
            await self.loop.run_in_executor(None, self.coord.get_current_transport_info)
            return True
        except Exception as exc:
            log.debug("[sonos] liveness probe failed: %r", exc)
            return False

    async def resubscribe(self, reason: str) -> None:
        log.warning("[sonos] resubscribing (%s)...", reason)
        old_sub = self.sub_box[0]
        if old_sub is not None:
            try:
                await old_sub.unsubscribe()
            except Exception as exc:
                log.debug("[sonos] old subscription cleanup error (non-fatal): %r", exc)
        self.listener.last_event_time = _time_module.monotonic()
        try:
            await self.subscribe_once()
        except Exception as exc:
            log.warning(
                "[sonos] subscribe on existing coord failed: %r — re-discovering zone %r",
                exc, self.zone_name,
            )
            new_coord = await self.loop.run_in_executor(None, self._find_zone, self.zone_name)
            if new_coord is None:
                raise RuntimeError(
                    f"zone {self.zone_name!r} not found during re-discovery"
                ) from exc
            self.coord = new_coord
            self.listener.coord = new_coord
            await self.subscribe_once()
        log.info("[sonos] resubscribed successfully")
        try:
            info = await self.loop.run_in_executor(None, self.coord.get_current_track_info)
            ti = await self.loop.run_in_executor(None, self.coord.get_current_transport_info)
            payload = self._build_initial_payload(self.coord, info or {}, ti or {})
            result = self.on_event(payload)
            if asyncio.iscoroutine(result):
                await result
            log.info(
                "[sonos] post-resubscribe reconcile: source=%s title=%r",
                payload.get("source"), payload.get("title"),
            )
        except Exception as exc:
            log.debug("[sonos] post-resubscribe reconcile failed (non-fatal): %r", exc)

    async def startup_probe(self) -> None:
        await asyncio.sleep(self._startup_timeout)
        if self.listener.last_event_time is None:
            log.warning(
                "[sonos] no NOTIFY received within %.0fs of subscribe "
                "— callback URL may be unreachable from the Sonos zone",
                self._startup_timeout,
            )
        else:
            log.info("[sonos] subscription verified (first NOTIFY received)")

    async def watchdog(self) -> None:
        consecutive_failures = 0
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(
                    self._renewal_failed.wait(),
                    timeout=self._watchdog_interval,
                )
                self._renewal_failed.clear()
                trigger_reason: str | None = "auto-renewal failure"
            except asyncio.TimeoutError:
                trigger_reason = None

            if self.stop.is_set():
                break

            consecutive_failures = await self._watchdog_tick(trigger_reason, consecutive_failures)

    async def _watchdog_tick(self, trigger_reason: str | None, consecutive_failures: int) -> int:  # skylos: ignore SKY-Q301 — Why: CC 13 maps directly to 4 distinct liveness paths (no-op, alive-no-resub, unreachable, resubscribe+fail-escalation); extracting sub-helpers would obscure the decision tree
        """Evaluate one watchdog tick; return updated consecutive_failures count."""
        last = self.listener.last_event_time
        elapsed = (_time_module.monotonic() - last) if last is not None else float("inf")

        log.debug(
            "[sonos] watchdog tick: trigger=%s elapsed=%.0fs threshold=%.0fs",
            trigger_reason or "periodic",
            elapsed,
            self._dead_timeout,
        )

        if trigger_reason is None and elapsed < self._dead_timeout:
            return consecutive_failures

        reason = trigger_reason or (
            f"no events for {elapsed:.0f}s (threshold={self._dead_timeout:.0f}s)"
        )
        log.warning("[sonos] watchdog: %s — running liveness probe", reason)

        alive = await self.probe_liveness()
        last_post = self.listener.last_event_time
        elapsed_post = (
            (_time_module.monotonic() - last_post) if last_post is not None else float("inf")
        )

        if alive and trigger_reason is None and elapsed_post < self._dead_timeout:
            log.debug("[sonos] watchdog: liveness OK, no resubscribe needed")
            return consecutive_failures

        if not alive:
            log.warning(
                "[sonos] watchdog: zone unreachable — skipping resubscribe "
                "(network issue; will retry on next tick)"
            )
            return consecutive_failures

        try:
            await self.resubscribe(reason)
            return 0
        except Exception as exc:
            consecutive_failures += 1
            log.error(
                "[sonos] watchdog: resubscribe failed (attempt %d/%d): %r",
                consecutive_failures,
                self._max_resub_failures,
                exc,
            )
            if consecutive_failures >= self._max_resub_failures:
                log.error(
                    "[sonos] watchdog: %d consecutive resubscribe failures — "
                    "raising to trigger service restart",
                    self._max_resub_failures,
                )
                raise
            return consecutive_failures
