"""Wikipedia album-context blurb fetcher.

Pulls a short (2–3 sentence) `extract` from the Wikipedia REST summary API
for the currently-playing album. Cached per release_id on disk so we don't
re-fetch on every track change inside the same record.

Cascade:
1. REST `/api/rest_v1/page/summary/{title}` with `"{album} ({artist} album)"`.
2. On 404, `action=opensearch` to find the best matching page title, then
   REST-summary that.

No API key. Wikipedia asks for a descriptive User-Agent (per
https://meta.wikimedia.org/wiki/User-Agent_policy) — same one we use for
MusicBrainz works.

Usage:
    from nowplaying import wiki
    payload = await wiki.get_or_fetch(release_id, artist, album)
    # payload is {"summary": str, "url": str, "title": str} or None
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import aiohttp

from nowplaying._io_safe import safe_read_bytes, safe_write_bytes

log = logging.getLogger("nowplaying.wiki")

USER_AGENT = "now-playing/1.0 (https://github.com/schuettc/now-playing)"

REST_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
OPENSEARCH_URL = "https://en.wikipedia.org/w/api.php"

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_DIR = REPO_ROOT / "pi" / "data" / "wiki"

# Bumped to v2 when artist-verification was added. v1 entries may contain
# cross-artist false positives (e.g. Slumber Party "Musik" pointing at
# Plastikman's page) and are silently treated as cache misses so the
# verified cascade re-resolves them.
CACHE_VERSION = 2

# In-memory negative cache: artist+album we've already missed on. Wikipedia
# pages don't appear overnight, but we want some self-healing so a 7d TTL
# matches what coverart.py does.
_NEGATIVE_TTL_S = 7 * 24 * 3600
_negative_cache: dict[tuple[str, str], float] = {}

# Words that don't carry artist identity. The verifier must find at least
# one *non*-stopword token from the artist string in the Wikipedia payload.
# Artists whose names consist entirely of stopwords (e.g. "The The") fail
# open — the verifier returns True when the post-filter token set is empty.
_ARTIST_STOPWORDS = frozenset({"the", "and", "a", "an", "of", "&"})
# `[^\W_]+` matches Unicode word characters so accents survive
# tokenization (e.g. "Sigur Rós" → ["sigur", "rós"] rather than
# ["sigur", "r", "s"], which would let the stray 's' false-match any
# apostrophe-s in prose).
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _artist_tokens(artist: str) -> list[str]:
    """Return the non-stopword tokens of an artist string, lowercased.

    Single-character tokens are dropped — they're either splitter
    artifacts from punctuation-bearing names (e.g. "Jane's Addiction"
    splitting to ["jane", "s", "addiction"]) or genuinely ambiguous
    one-letter band names like "X" or "M". Either way they're too
    noisy to use as a match signal; if every token is dropped, the
    verifier fails open.
    """
    return [
        t for t in _TOKEN_RE.findall((artist or "").lower())
        if t not in _ARTIST_STOPWORDS and len(t) > 1
    ]


def _payload_matches_artist(data: dict[str, Any], artist: str) -> bool:
    """True if the Wikipedia REST summary payload appears to describe an
    album by `artist`.

    Strategy: require at least one non-stopword artist token to appear in
    either `description` (high-signal, e.g. "1994 studio album by Richie
    Hawtin") or the first ~300 chars of `extract`. If the artist tokenizes
    to an empty set (e.g. "The The"), fail open — there's nothing to check
    against. If no artist is provided at all, also fail open (the caller
    isn't in a position to verify).
    """
    if not artist:
        return True
    tokens = _artist_tokens(artist)
    if not tokens:
        return True
    description = (data.get("description") or "").lower()
    extract_head = (data.get("extract") or "").lower()[:300]
    # Tokenize the haystack on word boundaries so short artist tokens
    # like "me", "us", "low" can't false-positive on substrings inside
    # ordinary prose ("someone", "used", "below").
    haystack_tokens = set(_TOKEN_RE.findall(f"{description} {extract_head}"))
    return any(t in haystack_tokens for t in tokens)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _negative_cached(artist: str, album: str) -> bool:
    key = (_norm(artist), _norm(album))
    stamp = _negative_cache.get(key)
    if stamp is None:
        return False
    if time.time() - stamp > _NEGATIVE_TTL_S:
        _negative_cache.pop(key, None)
        return False
    return True


def _mark_negative(artist: str, album: str) -> None:
    _negative_cache[(_norm(artist), _norm(album))] = time.time()


def _cache_path(release_id: int | str) -> Path:
    return WIKI_DIR / f"{release_id}.json"


def cached_summary(release_id: int | str) -> Optional[dict[str, Any]]:
    """Return the on-disk cached blurb for `release_id`, or None.

    Entries written before artist-verification existed (no `"v"` field, or
    `"v" < CACHE_VERSION`) are silently treated as misses so the verified
    cascade re-resolves them. This avoids serving stale wrong-artist
    blurbs after upgrade.
    """
    p = _cache_path(release_id)
    if not p.exists():
        return None
    try:
        data = json.loads(safe_read_bytes(p, max_bytes=256 * 1024).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("wiki cache read failed for %s: %r", release_id, e)
        return None
    if not isinstance(data, dict) or data.get("v", 0) < CACHE_VERSION:
        return None
    return data


def store_summary(release_id: int | str, payload: dict[str, Any]) -> None:
    """Write `payload` to disk under `release_id`. Best-effort: logs on failure."""
    try:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        stamped = {**payload, "v": CACHE_VERSION}
        safe_write_bytes(
            _cache_path(release_id),
            json.dumps(stamped, ensure_ascii=False).encode("utf-8"),
        )
    except OSError as e:
        log.warning("wiki cache write failed for %s: %r", release_id, e)


async def _rest_summary(
    title: str,
    *,
    session: aiohttp.ClientSession,
    artist: str | None = None,
) -> Optional[dict[str, Any]]:
    """Fetch the REST summary for a specific Wikipedia page title.

    Returns the parsed JSON dict or None on 404 / non-OK / transport error.
    Disambiguation pages are treated as misses — they don't have a useful
    extract and we don't want to render "X may refer to:" on the kiosk.

    When `artist` is provided, the raw payload is gated on
    `_payload_matches_artist` *before* the trimmed return dict is built.
    This catches cross-artist collisions where Wikipedia's generic
    disambiguators (e.g. `Musik (album)`) happen to point at a
    different artist's same-titled album.
    """
    url = REST_SUMMARY_URL.format(title=urllib.parse.quote(title, safe=""))
    try:
        async with session.get(url) as resp:  # skylos: ignore SKY-D216 — url built from module-level REST_SUMMARY_URL constant + urllib-quoted title; host is fixed (en.wikipedia.org)
            if resp.status != 200:
                return None
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("wiki rest summary failed for %r: %r", title, e)
        return None
    if data.get("type") == "disambiguation":
        return None
    extract = data.get("extract")
    if not extract:
        return None
    if artist and not _payload_matches_artist(data, artist):
        log.info(
            "wiki: rejected %r — page does not mention artist %r",
            title, artist,
        )
        return None
    return {
        "summary": extract,
        "url": (data.get("content_urls") or {}).get("desktop", {}).get("page")
        or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}",
        "title": data.get("title") or title,
    }


async def _opensearch_title(
    query: str,
    *,
    session: aiohttp.ClientSession,
) -> Optional[str]:
    """Use the MediaWiki opensearch endpoint to find the best matching page
    title for `query`. Returns the top result's title, or None."""
    params = {
        "action": "opensearch",
        "search": query,
        "limit": "1",
        "namespace": "0",
        "format": "json",
    }
    try:
        async with session.get(OPENSEARCH_URL, params=params) as resp:  # skylos: ignore SKY-D216 — OPENSEARCH_URL is a module-level constant (en.wikipedia.org/w/api.php); host is fixed, not user-controlled
            if resp.status != 200:
                return None
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("wiki opensearch failed for %r: %r", query, e)
        return None
    # opensearch shape: [query, [titles], [descs], [urls]]
    try:
        titles = data[1]
    except (IndexError, TypeError) as e:
        log.debug("wiki: opensearch payload shape unexpected: %r", e)
        return None
    if not titles:
        return None
    return titles[0]


async def fetch_summary(
    artist: str,
    album: str,
    *,
    timeout_s: float = 10.0,
) -> Optional[dict[str, Any]]:
    """Look up the Wikipedia summary for an album. Returns
    `{"summary": str, "url": str, "title": str}` or None.

    Two-step cascade: try the canonical `"{album} ({artist} album)"` title
    first, then fall back to opensearch on the artist+album+album phrase.
    """
    if not artist or not album:
        return None
    if _negative_cached(artist, album):
        return None

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # Step 1: candidate titles. The artist-bound disambiguators come
        # first; the artist-less forms are tried last *with verification*
        # so they're only accepted when Wikipedia happens to serve the
        # right artist's page (the common case for unique album titles
        # like "Siamese Dream" that have no disambiguator on Wikipedia).
        # The verifier rejects wrong-artist collisions before they
        # reach the cache.
        candidates = [
            f"{album} ({artist} album)",
            f"{album} ({artist})",
            f"{album} (album)",
            album,
        ]
        for title in candidates:
            payload = await _rest_summary(
                title, session=session, artist=artist,
            )
            if payload is not None:
                return payload

        # Step 2: opensearch fallback. The resulting title is also gated
        # on the artist verifier so a high-scoring but wrong-artist match
        # gets rejected.
        found = await _opensearch_title(
            f"{artist} {album} album", session=session,
        )
        if found:
            payload = await _rest_summary(
                found, session=session, artist=artist,
            )
            if payload is not None:
                return payload

    _mark_negative(artist, album)
    return None


async def get_or_fetch(
    release_id: int | str,
    artist: str,
    album: str,
    *,
    timeout_s: float = 10.0,
) -> Optional[dict[str, Any]]:
    """Return the cached blurb for `release_id`, fetching+storing on miss.

    Returns None if Wikipedia has nothing for this album. Negative results
    are *not* persisted to disk (they live in the in-memory negative cache
    for 7 days) — that lets a daemon restart re-try a missed album cheaply
    in case Wikipedia gained coverage.
    """
    cached = cached_summary(release_id)
    if cached is not None:
        return cached
    payload = await fetch_summary(artist, album, timeout_s=timeout_s)
    if payload is None:
        return None
    store_summary(release_id, payload)
    return payload
