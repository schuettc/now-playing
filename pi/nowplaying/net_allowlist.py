"""Shared upstream-host allowlist for any code that fetches user-supplied URLs.

The kiosk historically had one allowlist living inside ``api._is_allowed_upstream``
that ``art_cache_handler`` consulted before proxying Sonos's ``?u=`` URLs.
``art_overrides.set`` also needs to reject anything that isn't a known image
CDN, so the check lives here and both call sites import from it.

The allowlist is intentionally narrow: image CDNs we trust + Sonos's LAN
port. Anything else is rejected. Adding a host requires touching this file,
which makes the audit trail explicit.
"""
from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable
from urllib.parse import urljoin, urlparse

import aiohttp

# Canonical image-CDN hosts. Suffix match so ``a.mzstatic.com`` /
# ``b.mzstatic.com`` / etc. are all accepted under the parent.
HOST_ALLOWLIST: tuple[str, ...] = (
    "mzstatic.com",         # Apple Music CDN (legacy AirPlay path)
    "coverartarchive.org",  # MusicBrainz Cover Art Archive
    "img.discogs.com",      # Discogs scans (release-level images)
    "i.discogs.com",        # Discogs scans (master images, newer CDN)
)

# Hosts reachable only as the target of a redirect from an allowlisted
# URL. Cover Art Archive answers every image request with a redirect to
# archive.org, which redirects again to one of its storage nodes.
REDIRECT_HOST_ALLOWLIST: tuple[str, ...] = (
    "archive.org",
)

MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_SONOS_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class DisallowedRedirect(aiohttp.ClientError):
    """An upstream redirected somewhere the allowlist does not cover."""


def _is_lan_sonos(host: str, port: int | None) -> bool:
    """LAN Sonos addresses (192.168/16, 10/8, 172.16/12) on the standard
    Sonos HTTP port (1400). That's where AirPlay-through-Sonos serves
    cover art. The host must be an IP literal: a hostname that merely
    starts with ``10.`` resolves wherever its owner likes.
    """
    if port != 1400:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _SONOS_LAN_NETWORKS)


def _host_in(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def is_allowed_upstream(url: str) -> bool:
    """Return True when ``url`` targets an allowlisted host.

    Accepts http/https only. LAN Sonos addresses are also allowed via
    :func:`_is_lan_sonos`.
    """
    try:
        u = urlparse(url)
        port = u.port
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").lower()
    if not host:
        return False
    if _host_in(host, HOST_ALLOWLIST):
        return True
    return _is_lan_sonos(host, port)


def _is_allowed_redirect(url: str) -> bool:
    if is_allowed_upstream(url):
        return True
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and _host_in(
        (u.hostname or "").lower(), REDIRECT_HOST_ALLOWLIST,
    )


@asynccontextmanager
async def open_checked(
    request: Callable[..., Any], url: str, **kwargs: Any,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """Open ``url`` with ``request`` (a session's ``get`` or ``head``) and
    yield the final response, following redirects one hop at a time and
    checking every hop against the allowlist. The caller is responsible
    for checking ``url`` itself with :func:`is_allowed_upstream`.

    Raises :class:`DisallowedRedirect` when a hop leaves the allowlist,
    carries no ``Location``, or the chain exceeds ``MAX_REDIRECTS``.
    """
    for _ in range(MAX_REDIRECTS + 1):
        async with request(url, allow_redirects=False, **kwargs) as resp:  # skylos: ignore SKY-D216 — first url is allowlist-checked by the caller, every later hop by _is_allowed_redirect below
            if resp.status not in _REDIRECT_STATUSES:
                yield resp
                return
            location = resp.headers.get("Location")
        if not location:
            raise DisallowedRedirect(f"redirect without Location from {url}")
        url = urljoin(url, location)
        if not _is_allowed_redirect(url):
            raise DisallowedRedirect(f"redirect to non-allowlisted {url}")
    raise DisallowedRedirect(f"more than {MAX_REDIRECTS} redirects")
