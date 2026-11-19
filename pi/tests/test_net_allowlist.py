"""Allowlist guard for upstream art fetches."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import net_allowlist
from nowplaying.net_allowlist import is_allowed_upstream


@pytest.mark.parametrize("url", [
    "https://coverartarchive.org/release/abc/front",
    "https://i.discogs.com/abc/master.jpg",
    "https://img.discogs.com/abc/release.jpg",
    "https://a.mzstatic.com/artwork/foo.jpg",
    "http://192.168.1.40:1400/getaa?x=1",
    "http://10.0.0.5:1400/getaa?x=1",
    "http://172.16.5.5:1400/getaa?x=1",
])
def test_allowed(url):
    assert is_allowed_upstream(url) is True


@pytest.mark.parametrize("url", [
    "https://evil.example.com/img.jpg",
    "file:///etc/passwd",
    "http://localhost/admin",
    "http://192.168.1.40:8080/admin",   # private but not :1400
    "http://172.32.0.1:1400/x",         # outside 172.16-31
    "ftp://coverartarchive.org/x",       # wrong scheme
    "https://discogs.com.evil.tld/x",   # suffix match must not slip
    "http://10.attacker.tld:1400/x",    # hostname that only looks private
    "http://192.168.evil.tld:1400/x",
    "http://172.16.evil.tld:1400/x",
    "http://127.0.0.1:1400/x",          # loopback is not a Sonos speaker
    "http://169.254.1.1:1400/x",        # link-local
    "",
    "not-a-url",
])
def test_rejected(url):
    assert is_allowed_upstream(url) is False


class _Resp:  # skylos: ignore — test stand-in for aiohttp.ClientResponse
    def __init__(self, status=200, location=None):
        self.status = status
        self.headers = {"Location": location} if location else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _requester(responses):
    """A ``session.get`` stand-in that answers each call from ``responses``
    and records the URLs and the redirect flag it was called with."""
    calls = []

    def request(url, **kw):
        calls.append((url, kw.get("allow_redirects")))
        return responses.pop(0)

    return request, calls


def _open(request, url):
    import asyncio

    async def go():
        async with net_allowlist.open_checked(request, url) as resp:
            return resp.status

    return asyncio.new_event_loop().run_until_complete(go())


def test_redirect_to_disallowed_host_is_refused():
    request, calls = _requester([_Resp(302, "http://127.0.0.1:8080/admin")])
    with pytest.raises(net_allowlist.DisallowedRedirect):
        _open(request, "http://192.168.1.40:1400/getaa?x=1")
    assert calls == [("http://192.168.1.40:1400/getaa?x=1", False)]


def test_cover_art_archive_redirect_chain_is_followed():
    request, calls = _requester([
        _Resp(307, "https://archive.org/download/mbid-x/front.jpg"),
        _Resp(302, "https://ia800100.us.archive.org/1/items/mbid-x/front.jpg"),
        _Resp(200),
    ])
    assert _open(request, "https://coverartarchive.org/release/x/front") == 200
    assert [u for u, _ in calls][1:] == [
        "https://archive.org/download/mbid-x/front.jpg",
        "https://ia800100.us.archive.org/1/items/mbid-x/front.jpg",
    ]
    assert all(flag is False for _, flag in calls)


def test_archive_org_is_not_a_first_hop():
    assert is_allowed_upstream("https://archive.org/download/x/front.jpg") is False


def test_redirect_loop_is_refused():
    loop = [_Resp(302, "https://coverartarchive.org/a") for _ in range(20)]
    request, _ = _requester(loop)
    with pytest.raises(net_allowlist.DisallowedRedirect):
        _open(request, "https://coverartarchive.org/a")
