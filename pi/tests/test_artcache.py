"""Tests for the album-art cache and proxy logic."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import artcache  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    """Redirect the on-disk cache to a per-test tmp dir and reset locks."""
    monkeypatch.setattr(artcache, "CACHE_DIR", tmp_path / "cache")
    artcache._locks.clear()
    yield


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_key_for_case_insensitive_and_whitespace_trim():
    a = artcache.key_for("The Beatles", "Rubber Soul")
    b = artcache.key_for("the beatles  ", "rubber soul")
    c = artcache.key_for("THE BEATLES", "RUBBER SOUL")
    assert a == b == c
    assert len(a) == 16


def test_key_for_returns_none_on_missing_fields():
    assert artcache.key_for(None, "Album") is None
    assert artcache.key_for("Artist", "") is None
    assert artcache.key_for("", "") is None


def test_release_key_matches_existing_override_index_keys():
    """Override picks on disk are indexed under this hash; it must not drift."""
    assert artcache.key_for_release(4033122) == "f1db4e25edf56b1a"
    assert artcache.key_for_release(0) is None


def test_track_key_separates_tracks_sharing_one_session_url():
    url = "http://sonos/getaa?u=1149692896"
    glass = artcache.key_for_track("The Beatles", "The Beatles", "Glass Onion", url)
    assert glass == artcache.key_for_track(" the beatles", "THE BEATLES", "glass onion ", url)
    assert glass != artcache.key_for_track("The Beatles", "The Beatles", "Wild Honey Pie", url)
    assert glass != artcache.key_for_track("The Beatles", "The Beatles", "Glass Onion", url + "1")


def test_is_valid_key_format():
    assert artcache.is_valid_key("0123456789abcdef")
    assert not artcache.is_valid_key("0123456789abcde")     # too short
    assert not artcache.is_valid_key("0123456789abcdefg")    # too long
    assert not artcache.is_valid_key("zzzzzzzzzzzzzzzz")     # non-hex
    assert not artcache.is_valid_key("../../../etc/passwd")


class _MockResp:  # skylos: ignore — test fixture stand-in for aiohttp.ClientResponse
    def __init__(self, status=200, body=b"\x89PNG_DATA", ctype="image/jpeg"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": ctype}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._body

    @property
    def content(self):
        resp = self

        class _Stream:
            async def iter_chunked(self, n):
                body = await resp.read()
                for i in range(0, len(body), n):
                    yield body[i:i + n]

        return _Stream()


def _mock_session(resp: _MockResp) -> MagicMock:
    sess = MagicMock()
    sess.get = MagicMock(return_value=resp)
    return sess


def test_fetch_writes_to_disk_and_returns_bytes(tmp_path):
    resp = _MockResp(status=200, body=b"abc123", ctype="image/png")
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    data, ctype, status = _run(
        artcache.fetch_and_cache(key, "http://192.168.1.100:1400/getaa?u=1", session=sess)
    )
    assert status == 200
    assert data == b"abc123"
    assert ctype == "image/png"
    blob = artcache.CACHE_DIR / f"{key}.bin"
    mime = artcache.CACHE_DIR / f"{key}.type"
    assert blob.read_bytes() == b"abc123"
    assert mime.read_text() == "image/png"


def test_second_fetch_uses_cache_no_upstream_hit():
    resp = _MockResp()
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    _run(artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess))
    _run(artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess))
    assert sess.get.call_count == 1


def test_404_returned_as_404_no_cache():
    resp = _MockResp(status=404)
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    data, ctype, status = _run(
        artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess)
    )
    assert (data, ctype, status) == (None, None, 404)
    assert not (artcache.CACHE_DIR / f"{key}.bin").exists()


def test_500_returned_as_502_no_cache():
    resp = _MockResp(status=503)
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    data, _, status = _run(
        artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess)
    )
    assert status == 502
    assert data is None


def test_non_image_content_type_rejected():
    resp = _MockResp(status=200, body=b"<html>", ctype="text/html")
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    data, _, status = _run(
        artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess)
    )
    assert status == 502
    assert data is None


def test_concurrent_fetches_singleflight():
    """Two concurrent calls on the same key should only fire one upstream fetch."""
    fetch_count = [0]

    class _SlowResp(_MockResp):
        async def read(self):
            fetch_count[0] += 1
            await asyncio.sleep(0.05)
            return b"once"

    sess = MagicMock()
    sess.get = MagicMock(side_effect=lambda *a, **kw: _SlowResp())

    async def race():
        key = artcache.key_for("Artist", "Album")
        await asyncio.gather(
            artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess),
            artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess),
            artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess),
        )

    asyncio.new_event_loop().run_until_complete(race())
    assert fetch_count[0] == 1


def test_oversized_body_rejected_and_not_cached():
    resp = _MockResp(body=b"x" * (artcache.MAX_BYTES + 1))
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    data, _, status = _run(
        artcache.fetch_and_cache(key, "http://lan:1400/x", session=sess)
    )
    assert status == 502
    assert data is None
    assert not (artcache.CACHE_DIR / f"{key}.bin").exists()


def test_upstream_redirect_is_not_followed_off_the_allowlist():
    resp = _MockResp(status=302)
    resp.headers["Location"] = "http://127.0.0.1:8080/admin"
    sess = _mock_session(resp)
    key = artcache.key_for("Artist", "Album")
    data, _, status = _run(
        artcache.fetch_and_cache(key, "http://192.168.1.40:1400/x", session=sess)
    )
    assert (data, status) == (None, 502)
    assert sess.get.call_count == 1
    assert sess.get.call_args.kwargs["allow_redirects"] is False
