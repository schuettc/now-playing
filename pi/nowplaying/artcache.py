"""On-disk cache for album art, keyed by sha1(artist|album).

Solves the flicker problem on AirPlay-through-Sonos: Sonos rotates a
per-track `?u=...` parameter in its `getaa` URL even when consecutive
tracks share the same album cover. By rewriting payload `art_url` to
`/art-cache/<hash>?u=<original>` we give the kiosk a stable URL across
the whole album, so the browser cache wins and the <img> never reloads.

Each cached item is two files:
    pi/data/art/cache/<hash>.bin       — the raw image bytes
    pi/data/art/cache/<hash>.type      — the content-type, one line

We persist content-type so we don't have to guess on a cache hit. Image
formats from the upstream are heterogeneous (jpeg from Sonos, png from
Apple, webp from MB occasionally).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import aiohttp

log = logging.getLogger("nowplaying.artcache")

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pi" / "data" / "art" / "cache"

# One lock per key so concurrent /art-cache/<key> requests don't fire
# duplicate upstream fetches before the file lands on disk.
_locks: dict[str, asyncio.Lock] = {}


def _ensure_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def key_for(artist: str | None, album: str | None) -> str | None:
    """Stable 16-hex-char hash for an (artist, album) pair.

    Case- and whitespace-insensitive so "The Beatles" / "the beatles  " /
    "THE BEATLES" all collide. Returns None when either field is empty —
    callers should skip the rewrite in that case.
    """
    a = (artist or "").strip().lower()
    b = (album or "").strip().lower()
    if not a or not b:
        return None
    h = hashlib.sha1(f"{a}|{b}".encode("utf-8")).hexdigest()
    return h[:16]


def is_valid_key(key: str) -> bool:
    if len(key) != 16:
        return False
    try:
        int(key, 16)
        return True
    except ValueError:
        return False


def _paths(key: str) -> tuple[Path, Path]:
    return CACHE_DIR / f"{key}.bin", CACHE_DIR / f"{key}.type"


def read_cached(key: str) -> tuple[bytes, str] | None:
    """Return (bytes, content_type) if cached, else None. Never raises."""
    if not is_valid_key(key):
        return None
    blob, mime = _paths(key)
    if not blob.exists():
        return None
    try:
        data = blob.read_bytes()
        ctype = mime.read_text().strip() if mime.exists() else "image/jpeg"
        return data, ctype
    except OSError as e:
        log.warning("artcache read failed for %s: %r", key, e)
        return None


async def _read_image_response(
    resp: "aiohttp.ClientResponse", upstream_url: str,
) -> tuple[int, str | None, bytes | None]:
    """Validate the response and read the body. Returns (status, ctype, data).
    status is 200 on success; 404 / 502 on rejected responses (with ctype/data None)."""
    if resp.status == 404:
        return 404, None, None
    if resp.status != 200:
        log.warning(
            "artcache upstream non-200: status=%s url=%s",
            resp.status, upstream_url,
        )
        return 502, None, None
    ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    if not ctype.startswith("image/"):
        log.warning(
            "artcache upstream non-image content-type=%r url=%s",
            ctype, upstream_url,
        )
        return 502, None, None
    return 200, ctype, await resp.read()


async def _fetch_upstream(
    session: aiohttp.ClientSession,
    upstream_url: str,
    timeout_s: float,
) -> tuple[bytes, str, int] | tuple[None, None, int]:
    """Perform the upstream HTTP fetch and validate the response.

    Returns (bytes, content_type, 200) on success, or (None, None, status)
    where status is 404 (definitively missing) or 502 (transient / unusable).
    """
    try:
        async with session.get(  # skylos: ignore SKY-D216 — sole caller (api.art_cache_handler) gates upstream_url through is_allowed_upstream
            upstream_url, timeout=aiohttp.ClientTimeout(total=timeout_s)
        ) as resp:
            status, ctype, data = await _read_image_response(resp, upstream_url)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("artcache upstream fetch failed: %r url=%s", e, upstream_url)
        return None, None, 502
    if status != 200:
        return None, None, status
    return data, ctype, 200


async def fetch_and_cache(
    key: str,
    upstream_url: str,
    *,
    session: aiohttp.ClientSession,
    timeout_s: float = 8.0,
) -> tuple[bytes, str, int] | tuple[None, None, int]:
    """Fetch `upstream_url` and persist it under `key`. Returns
    `(bytes, content_type, http_status)` on success or `(None, None, status)`
    on failure. `status` distinguishes 404 (art truly missing upstream)
    from 502-equivalents (timeout, connection error, non-200) so the route
    handler can return the right code to the kiosk.
    """
    if not is_valid_key(key):
        return None, None, 400
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        existing = read_cached(key)
        if existing is not None:
            data, ctype = existing
            return data, ctype, 200
        fetched = await _fetch_upstream(session, upstream_url, timeout_s)
        if fetched[0] is None:
            return fetched
        data, ctype, _ = fetched
        # Persist inside the lock so a concurrent caller waiting on the
        # lock will find the file on its read_cached() check immediately
        # after release — singleflight guarantee.
        _ensure_dir()
        blob, mime = _paths(key)
        try:
            blob.write_bytes(data)
            mime.write_text(ctype)
        except OSError as e:
            log.warning("artcache write failed for %s: %r", key, e)
            # Still return the bytes — we have them in memory.
        return data, ctype, 200
