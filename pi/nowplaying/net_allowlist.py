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

from urllib.parse import urlparse

# Canonical image-CDN hosts. Suffix match so ``a.mzstatic.com`` /
# ``b.mzstatic.com`` / etc. are all accepted under the parent.
HOST_ALLOWLIST: tuple[str, ...] = (
    "mzstatic.com",         # Apple Music CDN (legacy AirPlay path)
    "coverartarchive.org",  # MusicBrainz Cover Art Archive
    "img.discogs.com",      # Discogs scans (release-level images)
    "i.discogs.com",        # Discogs scans (master images, newer CDN)
)


def _is_lan_sonos(host: str, port: int | None) -> bool:
    """LAN Sonos addresses (192.168/16, 10/8, 172.16-31) on the standard
    Sonos HTTP port (1400). That's where AirPlay-through-Sonos serves
    cover art.
    """
    if port != 1400:
        return False
    if host.startswith("192.168.") or host.startswith("10."):
        return True
    return any(host.startswith(f"172.{n}.") for n in range(16, 32))


def is_allowed_upstream(url: str) -> bool:
    """Return True when ``url`` targets an allowlisted host.

    Accepts http/https only. LAN Sonos addresses are also allowed via
    :func:`_is_lan_sonos`.
    """
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").lower()
    if not host:
        return False
    for suffix in HOST_ALLOWLIST:
        if host == suffix or host.endswith("." + suffix):
            return True
    return _is_lan_sonos(host, u.port)
