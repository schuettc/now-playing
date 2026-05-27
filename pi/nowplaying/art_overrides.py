"""Per-album user-chosen art overrides.

JSON index at ``pi/data/art/overrides/index.json`` keyed by
``artcache.key_for(artist, album)`` plus a local image cache at
``pi/data/art/overrides/<key>.<ext>``. The on-disk file insulates the
play-time ``/art/<id>`` path from the original CDN — once the user picks,
we never touch Discogs again at play time.

Key shape uses ``artcache.key_for`` so the existing case-/whitespace-
normalization is the single source of truth across art-related modules.

``set()`` validates ``url`` through ``net_allowlist.is_allowed_upstream``
and the response Content-Type before persisting anything. Partial writes
are impossible: on any failure the function raises and the index is left
untouched.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp

from nowplaying import artcache
from nowplaying.net_allowlist import is_allowed_upstream

log = logging.getLogger("nowplaying.art_overrides")

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERRIDES_DIR = REPO_ROOT / "pi" / "data" / "art" / "overrides"
INDEX_PATH = OVERRIDES_DIR / "index.json"

FETCH_TIMEOUT_S = 8.0
MAX_BYTES = 8 * 1024 * 1024  # 8 MiB — Discogs primary scans are usually <2 MiB.
# Minimum plausible size for a real album-art JPEG. A 600x600 photographic
# JPEG never compresses smaller than this even at aggressive quality. Anything
# under this floor is almost certainly a truncated download — reject rather
# than save a half-JPEG that will render as a top-only strip on the kiosk.
MIN_BYTES = 4 * 1024

# Browser-like headers. Cover Art Archive proxies to archive.org, which has
# been observed deprioritizing or short-responding non-browser clients
# (default aiohttp UA = "Python/3.X aiohttp/3.X"). Sending a real UA and a
# matching Accept header gets us the same response the kiosk's preview
# thumbnails get when they load CAA URLs directly from the browser.
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36 "
        "now-playing-kiosk (https://github.com/schuettc/now-playing)"
    ),
    "Accept": "image/webp,image/avif,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Retry schedule for transient truncations. Archive.org's CDN occasionally
# resets connections mid-stream; a clean retry usually succeeds. Three total
# attempts, with a small backoff so we don't hammer the upstream on a
# legitimately broken file.
_FETCH_RETRY_DELAYS_S = [0.25, 0.75]

# Process-local write lock. The index is small and writes are rare
# (single user, one tap per album), so one global lock is fine.
#
# Two locks for two purposes:
#   _write_lock (asyncio.Lock) — serializes async `set()` callers.
#   _index_lock (threading.Lock) — serializes the actual disk mutation
#     inside ``asyncio.to_thread`` so the sync ``clear()`` and the
#     async ``set()``'s offloaded write can't race each other. Both
#     paths acquire this lock before reading/writing index.json.
_write_lock = asyncio.Lock()
# RLock so `_do_disk_write` / `clear` can hold the lock and still call
# `_load_index()` (which now also acquires the lock to guard the
# cold-cache disk-read race).
_index_lock = threading.RLock()


class OverrideError(Exception):
    """Raised when ``set()`` cannot persist. The API layer surfaces this
    to the kiosk so the user knows their choice did not save."""

    def __init__(
        self,
        reason: str,
        *,
        status: int = 502,
        permanent: bool = False,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status  # HTTP status the API endpoint should return
        # Permanent failures (4xx, oversized, non-image content-type) won't
        # get better on retry — short-circuit the retry loop on these.
        self.permanent = permanent


@dataclass(frozen=True)
class Override:
    key: str
    url: str
    source: str
    picked_at: str
    local_path: Optional[str]
    content_type: str = "image/jpeg"
    picked_at_epoch: int = 0


def key_for(artist: str | None, album: str | None) -> Optional[str]:
    """Same normalization the rest of the art pipeline uses
    (``artcache.key_for``). Returns None on missing fields."""
    return artcache.key_for(artist, album)


def _ensure_dir() -> None:
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)


# Module-level in-memory cache of the on-disk index. None means
# "never loaded yet"; an empty dict is a valid loaded-but-empty state.
# Population is lazy on first `get()` / `set()` / `clear()` call;
# mutations to the cache happen under `_index_lock` so concurrent
# readers and writers stay coherent. Invalidated (set back to None) by
# `_invalidate_index_cache()` after every disk write so the next read
# repopulates from the canonical on-disk state.
_cached_index: dict[str, dict] | None = None


def _load_index() -> dict[str, dict]:
    """Return the index, populating the in-memory cache on first call.

    Hot path: `get()` is called from `_rewrite_art_url_for_overrides`
    on every Sonos event (track/volume/position/poll). Without the
    cache that would mean reading + parsing `index.json` from disk
    on every Sonos event. With the cache, only the first call after
    startup or after an `set()`/`clear()` invalidation hits disk.

    The lock guards both the cache-population race (two `to_thread`
    calls landing on a cold cache could otherwise both hit disk) and
    the cache-write coherence with `_invalidate_index_cache`.
    """
    global _cached_index
    with _index_lock:
        if _cached_index is not None:
            return _cached_index
        if not INDEX_PATH.exists():
            _cached_index = {}
            return _cached_index
        try:
            _cached_index = json.loads(INDEX_PATH.read_text() or "{}")
        except (json.JSONDecodeError, OSError) as e:
            log.warning("art_overrides: index unreadable, treating as empty: %r", e)
            _cached_index = {}
        return _cached_index


def _invalidate_index_cache() -> None:
    """Drop the in-memory cache so the next `_load_index()` call
    re-reads from disk. Called after every successful index write
    (set / clear) so subsequent reads see fresh data."""
    global _cached_index
    with _index_lock:
        _cached_index = None


def prewarm() -> None:
    """Force-populate the in-memory cache. Called from orchestrator
    startup so the first Sonos event after boot doesn't pay a disk
    read on the main event loop. After prewarm, the only path that
    can re-hit disk is invalidation via set()/clear() (themselves
    serialized through `_index_lock` inside `asyncio.to_thread`)."""
    _load_index()


def _atomic_write_index(index: dict[str, dict]) -> None:
    _ensure_dir()
    fd, tmp_path = tempfile.mkstemp(
        prefix=".index-", suffix=".json", dir=OVERRIDES_DIR,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(index, f, indent=2, sort_keys=True)
        os.replace(tmp_path, INDEX_PATH)
    except OSError:
        _silent_unlink(tmp_path)
        raise


def _silent_unlink(path: str | os.PathLike) -> None:
    """Best-effort unlink; logs at debug when the file is already gone or
    the FS refuses the removal. Used in cleanup paths where the caller is
    already raising or has nothing better to do with the failure."""
    try:
        os.unlink(path)  # skylos: ignore SKY-D215 — path is round-tripped from our own JSON index entries under OVERRIDES_DIR; not user input
    except OSError as e:
        log.debug("art_overrides: unlink %s failed: %r", path, e)


def _ext_from_content_type(ctype: str) -> str:
    ctype = (ctype or "").lower()
    if "png" in ctype:
        return "png"
    if "webp" in ctype:
        return "webp"
    if "gif" in ctype:
        return "gif"
    return "jpg"


def get(artist: str, album: str) -> Optional[Override]:
    key = key_for(artist, album)
    if not key:
        return None
    rec = _load_index().get(key)
    if not rec:
        return None
    return Override(
        key=key,
        url=rec["url"],
        source=rec["source"],
        picked_at=rec["picked_at"],
        local_path=rec.get("local_path"),
        content_type=rec.get("content_type") or "image/jpeg",
        picked_at_epoch=int(rec.get("picked_at_epoch") or 0),
    )


def _validate_response_meta(status: int, ctype: str) -> None:
    """Raise OverrideError if the response status or content-type is bad.

    Both failures are permanent — a 404 won't become a 200 on retry, and
    an HTML body won't become a JPEG on retry.
    """
    if status != 200:
        raise OverrideError(
            f"upstream returned status {status}", status=502, permanent=True,
        )
    if not ctype.startswith("image/"):
        raise OverrideError(
            f"upstream content-type {ctype!r} is not an image",
            status=502, permanent=True,
        )


def _validate_body(
    data: bytes,
    *,
    declared_length: int | None = None,
    content_encoding: str = "",
) -> None:
    """Raise OverrideError on an empty, oversized, undersized, or
    truncated body.

    ``declared_length`` is the upstream ``Content-Length`` header (when
    present); we cross-check it against the actual body size.
    ``content_encoding`` skips the cross-check when the body was
    transparently decoded (e.g. gzip) — Content-Length describes the
    encoded form, ``data`` is the decoded form, so they will legitimately
    differ.

    Empty/oversized/non-image cases are permanent (won't improve on
    retry); truncated and undersized cases are transient (the truncation
    log so far has been archive.org's CDN resetting mid-stream).
    """
    if not data:
        raise OverrideError(
            "upstream returned empty body", status=502, permanent=True,
        )
    if len(data) > MAX_BYTES:
        raise OverrideError(
            f"upstream payload exceeds {MAX_BYTES} bytes",
            status=502, permanent=True,
        )
    # Belt-and-suspenders: when the upstream declared a Content-Length
    # and the response wasn't transparently decoded, the body length must
    # match. `resp.read()` already raises ClientPayloadError on short
    # reads, but this catches the case where the buffer was assembled by
    # a different code path or a future refactor.
    if declared_length is not None and not content_encoding:
        if len(data) != declared_length:
            raise OverrideError(
                f"truncated response: got {len(data)} bytes, "
                f"declared {declared_length}",
                status=502,
            )
    if len(data) < MIN_BYTES:
        raise OverrideError(
            f"response too small to be a real image "
            f"({len(data)} bytes; floor {MIN_BYTES})",
            status=502,
        )


async def _fetch_override_payload(
    url: str, *, session: aiohttp.ClientSession,
) -> tuple[bytes, str]:
    """One fetch attempt. Returns ``(bytes, content_type)``. Raises
    ``OverrideError`` on any non-2xx/wrong-type/empty/oversized/truncated/
    transport error. Permanent failures (4xx, oversized, non-image) set
    ``OverrideError.permanent = True`` so the retry loop can short-circuit.

    Use ``resp.read()`` (full-body read), NOT ``resp.content.read(n)`` —
    the latter is the low-level StreamReader API that returns whatever
    bytes happen to be in the buffer at EOF without validating against
    Content-Length, so a CDN closing the connection mid-stream silently
    yields a partial JPEG (the bug that caused saved overrides to render
    as top-only strips).
    """
    try:
        async with session.get(  # skylos: ignore SKY-D216 — url already validated by is_allowed_upstream() above
            url,
            timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S),
            headers=_FETCH_HEADERS,
        ) as resp:
            return await _process_override_response(resp)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise OverrideError(f"upstream fetch failed: {e!r}", status=502) from e


def _parse_declared_length(declared_raw: str | None, encoded: str) -> int | None:
    """Parse Content-Length when present and the response isn't compressed
    (Content-Encoding makes Content-Length refer to the compressed body,
    which doesn't match what we'd validate)."""
    if declared_raw is None or encoded:
        return None
    try:
        return int(declared_raw)
    except ValueError:
        return None


async def _read_validated_body(
    resp: aiohttp.ClientResponse, declared: int | None, encoded: str,
) -> bytes:
    """Body read with narrow ClientPayloadError catch + size validation."""
    try:
        data = await resp.read()
    except aiohttp.ClientPayloadError as e:
        raise OverrideError(
            f"truncated response (ClientPayloadError): {e}",
            status=502,
        ) from e
    _validate_body(data, declared_length=declared, content_encoding=encoded)
    return data


async def _process_override_response(
    resp: aiohttp.ClientResponse,
) -> tuple[bytes, str]:
    """All in-context-manager work for one response: meta validation,
    Content-Length pre-flight (skip buffering oversized payloads), body
    read+validate. Kept out of `_fetch_override_payload`'s network
    try-block so the outer try wraps only the actual session.get."""
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    _validate_response_meta(resp.status, ctype)
    encoded = (resp.headers.get("Content-Encoding") or "").strip().lower()
    declared = _parse_declared_length(resp.headers.get("Content-Length"), encoded)
    if declared is not None and declared > MAX_BYTES:
        raise OverrideError(
            f"upstream payload exceeds {MAX_BYTES} bytes (declared {declared})",
            status=502, permanent=True,
        )
    data = await _read_validated_body(resp, declared, encoded)
    return data, ctype


async def _fetch_with_retry(
    url: str, *, session: aiohttp.ClientSession,
) -> tuple[bytes, str]:
    """Retry transient truncations/network errors. Permanent failures
    (4xx, non-image content-type, oversized body) are raised immediately
    without retrying — those won't get better.
    """
    last: OverrideError | None = None
    delays = [0.0, *_FETCH_RETRY_DELAYS_S]
    for attempt, delay in enumerate(delays):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            return await _fetch_override_payload(url, session=session)
        except OverrideError as e:
            if e.permanent:
                raise
            last = e
            log.warning(
                "art_overrides: fetch attempt %d/%d failed: %s",
                attempt + 1, len(delays), e,
            )
    assert last is not None  # the loop always runs at least once
    raise last


def _remove_stale_key_files(key: str, keep_name: str) -> None:
    """Remove existing override files for ``key`` except ``keep_name`` and
    any in-flight ``.<key>-`` temp files. Per-file failures are logged at
    debug so one locked file doesn't abort the override write."""
    for old in OVERRIDES_DIR.glob(f"{key}.*"):
        if old.name == keep_name or old.name.startswith(f".{key}-"):
            continue
        try:
            old.unlink()
        except OSError as e:
            log.debug("art_overrides: stale file %s unlink failed: %r", old, e)


def _write_override_file(key: str, ext: str, data: bytes) -> Path:
    """Atomically write ``data`` to ``<OVERRIDES_DIR>/<key>.<ext>``.

    Returns the final path. Raises ``OSError`` on failure; the temp file
    is cleaned up via ``_silent_unlink`` before the exception propagates."""
    local_path = OVERRIDES_DIR / f"{key}.{ext}"
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{key}-", suffix=f".{ext}.part", dir=OVERRIDES_DIR,
    )
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
        _remove_stale_key_files(key, f"{key}.{ext}")
        os.replace(tmp_path, local_path)
    except OSError:
        _silent_unlink(tmp_path)
        raise
    return local_path


def _persist_override(
    key: str,
    ext: str,
    data: bytes,
    *,
    url: str,
    source: str,
    ctype: str,
    picked_at: str,
    epoch: int,
) -> str:
    """Write the image bytes and update the JSON index under
    ``_index_lock``. Returns the final on-disk path as a string."""
    with _index_lock:
        _ensure_dir()
        local_path = _write_override_file(key, ext, data)
        index = _load_index()
        index[key] = {
            "url": url,
            "source": source,
            "picked_at": picked_at,
            "picked_at_epoch": epoch,
            "local_path": str(local_path),
            "content_type": ctype,
        }
        _atomic_write_index(index)
        _invalidate_index_cache()
        return str(local_path)


async def set(
    artist: str,
    album: str,
    url: str,
    source: str,
    *,
    session: aiohttp.ClientSession,
) -> Override:
    """Fetch ``url``, persist the bytes, write the index entry. Atomic:
    either everything lands or nothing does.

    Raises ``OverrideError`` on missing artist/album (400), allowlist
    rejection (400), non-2xx/timeout/network/non-image/empty/oversized/
    truncated response (502), or local write failure (500).
    """
    key = key_for(artist, album)
    if not key:
        raise OverrideError("artist and album are required", status=400)
    if not is_allowed_upstream(url):
        raise OverrideError(
            "url host is not in the allowlist", status=400,
        )

    data, ctype = await _fetch_with_retry(url, session=session)

    ext = _ext_from_content_type(ctype)
    picked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    epoch = int(time.time())

    async with _write_lock:
        try:
            local_path_str = await asyncio.to_thread(
                _persist_override, key, ext, data,
                url=url, source=source, ctype=ctype,
                picked_at=picked_at, epoch=epoch,
            )
        except OSError as e:
            raise OverrideError(f"local write failed: {e!r}", status=500) from e

    log.info(
        "art_overrides: set key=%s source=%s bytes=%d", key, source, len(data),
    )
    return Override(
        key=key, url=url, source=source, picked_at=picked_at,
        local_path=local_path_str,
        content_type=ctype,
        picked_at_epoch=epoch,
    )


def clear(artist: str, album: str) -> bool:
    """Remove the override and its cached file. Returns True if anything
    was actually removed.

    Synchronous on purpose — the API DELETE handler dispatches this via
    ``asyncio.to_thread``. Index mutation is guarded by ``_index_lock``
    so a concurrent ``set()`` (also under the lock inside ``to_thread``)
    cannot interleave a read-modify-write.
    """
    key = key_for(artist, album)
    if not key:
        return False
    with _index_lock:
        index = _load_index()
        rec = index.pop(key, None)
        removed_file = False
        if rec:
            try:
                _atomic_write_index(index)
                _invalidate_index_cache()
            except OSError as e:
                log.warning("art_overrides: index write failed on clear: %r", e)
            local = rec.get("local_path")
            if local:
                try:
                    Path(local).unlink(missing_ok=True)  # skylos: ignore SKY-D215 — local_path is round-tripped from our JSON index under OVERRIDES_DIR, written by set() with a key_for()-derived filename; not user input
                    removed_file = True
                except OSError as e:
                    log.warning(
                        "art_overrides: clear local_path %s failed: %r",
                        local, e,
                    )
        # Also remove orphan files for this key (e.g., user wiped index but
        # not the image).
        for f in OVERRIDES_DIR.glob(f"{key}.*"):
            try:
                f.unlink()
                removed_file = True
            except OSError as e:
                log.debug(
                    "art_overrides: clear orphan %s unlink failed: %r", f, e,
                )
        return rec is not None or removed_file


def read_bytes(artist: str, album: str) -> Optional[tuple[bytes, str, int]]:
    """Return (bytes, content_type, picked_at_epoch) when an override is
    present AND its local file exists. Returns None when the override is
    missing OR the local file vanished (caller should ``clear`` and fall
    through to cascade in the latter case).

    Synchronous on purpose — the aiohttp art handler dispatches this via
    ``asyncio.to_thread`` so the event loop never blocks on the JSON
    index read or the image read.
    """
    ov = get(artist, album)
    if not ov or not ov.local_path:
        return None
    p = Path(ov.local_path)
    if not p.exists():
        return None
    try:
        data = p.read_bytes()  # skylos: ignore SKY-D215 — p comes from ov.local_path, our own JSON index value (see clear() above)
    except OSError as e:
        log.debug("art_overrides: read_bytes %s failed: %r", p, e)
        return None
    return data, ov.content_type, ov.picked_at_epoch
