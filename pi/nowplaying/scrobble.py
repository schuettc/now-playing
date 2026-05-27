"""Last.fm scrobbling — minimal stdlib + aiohttp implementation.

Sends `track.scrobble` (confirmed plays) and `track.updateNowPlaying` (current
track notifications) to https://ws.audioscrobbler.com/2.0/.

Configuration via env vars:

    LASTFM_API_KEY
    LASTFM_API_SECRET
    LASTFM_SESSION_KEY

When any of these are missing/empty, the module no-ops gracefully: both
public coroutines return False without performing HTTP, and never raise.
A single INFO log line is emitted on first call describing the state.

Signature: MD5 of the concatenation of `key + value` for each param sorted
by key (excluding `format` and `callback`), followed by the shared secret.
Encode the concatenated string as UTF-8 before hashing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Any, Optional

import aiohttp

log = logging.getLogger("nowplaying.scrobble")

API_URL = "https://ws.audioscrobbler.com/2.0/"

_logged_state = False


def _env() -> tuple[str, str, str]:
    return (
        (os.environ.get("LASTFM_API_KEY") or "").strip(),
        (os.environ.get("LASTFM_API_SECRET") or "").strip(),
        (os.environ.get("LASTFM_SESSION_KEY") or "").strip(),
    )


def _log_state_once(*, enabled: bool) -> None:
    global _logged_state
    if _logged_state:
        return
    _logged_state = True
    if enabled:
        log.info("Last.fm scrobbling enabled")
    else:
        log.info(
            "Last.fm scrobbling disabled (set LASTFM_API_KEY, "
            "LASTFM_API_SECRET, LASTFM_SESSION_KEY in pi/.env to enable)"
        )


_SIGN_EXCLUDED_KEYS = frozenset({"format", "callback"})


def _sign(params: dict[str, str], secret: str) -> str:
    """Last.fm signature: MD5 over sorted-key concatenation of (key+value)
    pairs, EXCLUDING `format` and `callback`, with `secret` appended.
    """
    signable = sorted(
        (k, params[k]) for k in params if k not in _SIGN_EXCLUDED_KEYS
    )
    blob = "".join(f"{k}{v}" for k, v in signable) + secret
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def _build(method: str, api_key: str, session_key: str, secret: str,
           extra: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {
        "method": method,
        "api_key": api_key,
        "sk": session_key,
    }
    for k, v in extra.items():
        if v is None:
            continue
        params[k] = str(v)
    params["api_sig"] = _sign(params, secret)
    params["format"] = "json"
    return params


async def _do_post(method: str, params: dict[str, str], *,
                   session: aiohttp.ClientSession) -> bool:
    """Issue the signed POST and interpret the response. Returns True only
    when Last.fm returned HTTP 200 AND the body has no ``error`` key."""
    async with session.post(API_URL, data=params, timeout=10) as resp:
        text = await resp.text()
        if resp.status != 200:
            log.warning(
                "Last.fm %s HTTP %s: %s", method, resp.status, text[:200]
            )
            return False
        # Body is JSON; error responses carry an "error" key with int code.
        body = _safe_json(text)
        if isinstance(body, dict) and body.get("error"):
            log.warning(
                "Last.fm %s error %s: %s",
                method, body.get("error"), body.get("message"),
            )
            return False
        return True


async def _post(method: str, extra: dict[str, Any], *,
                session: aiohttp.ClientSession) -> bool:
    api_key, secret, session_key = _env()
    if not (api_key and secret and session_key):
        _log_state_once(enabled=False)
        return False
    _log_state_once(enabled=True)

    params = _build(method, api_key, session_key, secret, extra)
    try:
        return await _do_post(method, params, session=session)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Last.fm %s failed: %r", method, e)
        return False


def _safe_json(text: str) -> Optional[dict]:
    """Parse a Last.fm response body. Returns the decoded ``dict`` on
    success, or ``None`` on any decode error (e.g. text/xml). aiohttp's
    ``resp.json()`` consumes the body and enforces a content-type check
    Last.fm sometimes violates, so we parse the captured text manually.
    """
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as e:
        log.debug("Last.fm non-JSON body: %r (head=%r)", e, text[:80])
        return None
    return decoded if isinstance(decoded, dict) else None


async def scrobble(artist: str, title: str, album: str | None,
                   timestamp: int, *,
                   session: aiohttp.ClientSession) -> bool:
    """Submit a `track.scrobble`. Returns True on success, False on any
    failure or when env vars are missing. Never raises."""
    if not artist or not title:
        return False
    ok = await _post(
        "track.scrobble",
        {
            "artist": artist,
            "track": title,
            "album": album,
            "timestamp": int(timestamp),
        },
        session=session,
    )
    if ok:
        log.info("Scrobbled to Last.fm: %s — %s", artist, title)
    return ok


async def update_now_playing(artist: str, title: str, album: str | None, *,
                              session: aiohttp.ClientSession) -> bool:
    """Notify Last.fm that this track is currently playing. Returns True
    on success, False on any failure or when env vars are missing.
    Never raises."""
    if not artist or not title:
        return False
    ok = await _post(
        "track.updateNowPlaying",
        {"artist": artist, "track": title, "album": album},
        session=session,
    )
    if ok:
        log.debug("Last.fm now-playing: %s — %s", artist, title)
    return ok
