"""Discogs live API image fetchers — master + release cover art.

Used only by the picker's candidate aggregator (``art_picker``). The default
``/art/<id>`` resolution path stays on MusicBrainz/CAA; Discogs is only
consulted when the user opens the picker, so an empty ``DISCOGS_TOKEN`` is
silently degraded (no candidates) rather than fatal.

The catalog snapshot in ``pi/data/discogs.sqlite`` does not store
``master_id``, so this module hits the live API to resolve it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, TypedDict

import aiohttp

log = logging.getLogger("nowplaying.discogs.images")

USER_AGENT = "now-playing/0.1 (+https://github.com/schuettc/now-playing)"
RELEASE_URL = "https://api.discogs.com/releases/{id}"
MASTER_URL = "https://api.discogs.com/masters/{id}"

# Discogs authenticated cap is 60/min. Per-source budget set by the
# aggregator's semaphore (Semaphore(2)) keeps us well under.
REQUEST_TIMEOUT_S = 3.0


class ImageRef(TypedDict, total=False):
    url: str
    type: str          # "primary" | "secondary"
    width: int
    height: int


def _token() -> str:
    return (os.environ.get("DISCOGS_TOKEN") or "").strip()


def _headers() -> dict[str, str]:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = _token()
    if token:
        h["Authorization"] = f"Discogs token={token}"
    return h


async def _get_json(
    session: aiohttp.ClientSession, url: str, label: str,
) -> Optional[dict[str, Any]]:
    """Fetch JSON. Returns None on any failure — picker should still render
    whatever it has from other sources."""
    if not _token():
        # The release/master endpoints permit unauthenticated reads at a
        # very low rate (and only for some data), but image URLs are gated
        # by auth. Skip rather than burn a tokenless request.
        log.debug("discogs images: DISCOGS_TOKEN not set, skipping %s", label)
        return None
    try:
        async with session.get(  # skylos: ignore SKY-D216 — url built from hardcoded api.discogs.com templates; only release/master IDs interpolated
            url,
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S),
        ) as resp:
            if resp.status == 429:
                log.warning("discogs images: rate-limited on %s", label)
                return None
            if resp.status != 200:
                log.info(
                    "discogs images: %s status=%s url=%s", label, resp.status, url,
                )
                return None
            return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.info("discogs images: %s failed: %r", label, e)
        return None


def _extract_images(payload: dict[str, Any]) -> list[ImageRef]:
    """Pull the ``images`` array out of a release/master payload. Discogs
    returns primary first, then secondaries. Each image looks like:
    ``{"type": "primary"|"secondary", "uri": ..., "uri150": ..., "width": ..., "height": ...}``
    """
    out: list[ImageRef] = []
    for img in payload.get("images") or ():
        uri = img.get("uri") or img.get("resource_url")
        if not uri:
            continue
        ref: ImageRef = {"url": uri, "type": img.get("type") or "secondary"}
        w = img.get("width")
        h = img.get("height")
        if isinstance(w, int):
            ref["width"] = w
        if isinstance(h, int):
            ref["height"] = h
        out.append(ref)
    # Primary first.
    out.sort(key=lambda r: 0 if r.get("type") == "primary" else 1)
    return out


async def resolve_master_id(
    session: aiohttp.ClientSession, release_id: int,
) -> Optional[int]:
    payload = await _get_json(
        session, RELEASE_URL.format(id=release_id), f"release={release_id}",
    )
    if not payload:
        return None
    mid = payload.get("master_id")
    if isinstance(mid, int) and mid > 0:
        return mid
    return None


async def _fetch_entity_images(
    session: aiohttp.ClientSession,
    *,
    kind: str,
    entity_id: int,
) -> list[ImageRef]:
    """Shared GET + extract for the two image endpoints. ``kind`` selects
    the URL template and the log label so both public wrappers reduce to
    a single call site."""
    template = MASTER_URL if kind == "master" else RELEASE_URL
    payload = await _get_json(
        session, template.format(id=entity_id), f"{kind}={entity_id}",
    )
    return _extract_images(payload) if payload else []


async def fetch_master_images(
    session: aiohttp.ClientSession, master_id: int,
) -> list[ImageRef]:
    return await _fetch_entity_images(
        session, kind="master", entity_id=master_id,
    )


async def fetch_release_images(
    session: aiohttp.ClientSession, release_id: int,
) -> list[ImageRef]:
    return await _fetch_entity_images(
        session, kind="release", entity_id=release_id,
    )


