"""Allowlist guard for upstream art fetches."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    "",
    "not-a-url",
])
def test_rejected(url):
    assert is_allowed_upstream(url) is False
