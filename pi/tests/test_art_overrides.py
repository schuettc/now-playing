"""Round-trip tests for the per-album art override store."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import art_overrides  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _isolate_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(art_overrides, "OVERRIDES_DIR", tmp_path)
    monkeypatch.setattr(art_overrides, "INDEX_PATH", tmp_path / "index.json")
    yield


# Minimum body that passes the orchestrator's "too small to be a real
# image" floor. Real album-art JPEGs are always at least a few KB; this
# pads a tiny JPEG header out so happy-path tests aren't fighting the
# truncation guard.
_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"x" * (8 * 1024)


class _Resp:  # skylos: ignore — test fixture stand-in for aiohttp.ClientResponse; method grouping mirrors the mocked surface, not application cohesion
    def __init__(
        self,
        status=200,
        ctype="image/jpeg",
        body=_FAKE_JPEG,
        *,
        declared_length=None,
        content_encoding=None,
    ):
        self.status = status
        self.headers = {"Content-Type": ctype}
        # Content-Length defaults to the actual body length so tests that
        # don't care about truncation get a self-consistent response; tests
        # that exercise the truncation guard can pass `declared_length`
        # explicitly to simulate a CDN-aborted download.
        if declared_length is None:
            self.headers["Content-Length"] = str(len(body))
        else:
            self.headers["Content-Length"] = str(declared_length)
        if content_encoding is not None:
            self.headers["Content-Encoding"] = content_encoding
        self._body = body
        # The orchestrator now calls `resp.read()` (no args) to get the full
        # body with built-in Content-Length validation. Mock that on the
        # response itself. Keep `content.read(n)` available too in case any
        # callers still use it.
        self.content = MagicMock()

        async def _content_read(_n):
            return self._body
        self.content.read = _content_read

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """Returns a single response for every .get() call."""
    def __init__(self, resp):
        self._resp = resp

    def get(self, _url, **_kw):
        return self._resp


class _SequenceSession:
    """Returns each response in order across successive .get() calls so a
    test can simulate one truncated attempt followed by a clean retry."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, _url, **_kw):
        self.calls += 1
        return self._responses.pop(0)


def test_set_and_get_roundtrip(tmp_path):
    session = _Session(_Resp())
    ov = _run(art_overrides.set(
        "The Beatles", "The Beatles",
        "https://i.discogs.com/master.jpg",
        "discogs-master",
        session=session,
    ))
    assert ov.key
    got = art_overrides.get("THE BEATLES", "the beatles  ")  # case-insensitive
    assert got is not None
    assert got.key == ov.key
    assert got.source == "discogs-master"
    assert Path(got.local_path).exists()

    bundle = art_overrides.read_bytes("The Beatles", "The Beatles")
    assert bundle is not None
    data, ctype, epoch = bundle
    assert data == _FAKE_JPEG
    assert ctype == "image/jpeg"
    assert epoch > 0


def test_set_rejects_truncated_response():
    # Upstream declared a larger Content-Length than it actually delivered —
    # this is the CDN-abort case that has caused 4-9 KB JPEG files on the
    # Pi to render as a top-only strip on the kiosk.
    session = _Session(_Resp(declared_length=len(_FAKE_JPEG) * 4))
    with pytest.raises(art_overrides.OverrideError) as exc:
        _run(art_overrides.set(
            "Artist", "Album",
            "https://i.discogs.com/truncated.jpg",
            "discogs-master", session=session,
        ))
    assert exc.value.status == 502
    assert "truncated" in str(exc.value).lower()


def test_set_skips_content_length_check_when_gzipped():
    # When the upstream uses Content-Encoding: gzip, aiohttp transparently
    # decompresses the body before we see it — the on-wire Content-Length
    # won't match len(data). The truncation check must be skipped in that
    # case rather than producing a false positive.
    session = _Session(_Resp(
        declared_length=999,  # ignored due to content-encoding
        content_encoding="gzip",
    ))
    ov = _run(art_overrides.set(
        "Artist", "Album",
        "https://i.discogs.com/gz.jpg",
        "discogs-master", session=session,
    ))
    assert ov.key


def test_set_rejects_too_small_response():
    # A real album-art JPEG is never <4 KB. Reject sub-floor responses
    # rather than save a half-decoded file.
    tiny = b"\xff\xd8\xff\xe0" + b"x" * 128
    session = _Session(_Resp(body=tiny))
    with pytest.raises(art_overrides.OverrideError) as exc:
        _run(art_overrides.set(
            "Artist", "Album",
            "https://i.discogs.com/tiny.jpg",
            "discogs-master", session=session,
        ))
    assert exc.value.status == 502
    assert "too small" in str(exc.value).lower()


def test_set_retries_after_truncation(monkeypatch):
    # Simulate archive.org's intermittent connection-reset: first attempt
    # truncated, second attempt clean. The retry loop should swallow the
    # first failure and persist the second response.
    # Speed up the test by zeroing the inter-attempt backoff.
    monkeypatch.setattr(art_overrides, "_FETCH_RETRY_DELAYS_S", [0.0, 0.0])
    truncated = _Resp(declared_length=len(_FAKE_JPEG) * 4)
    clean = _Resp()
    session = _SequenceSession([truncated, clean])
    ov = _run(art_overrides.set(
        "Artist", "Album",
        "https://i.discogs.com/flaky.jpg",
        "discogs-master", session=session,
    ))
    assert ov.key
    assert session.calls == 2


def test_set_does_not_retry_permanent_errors(monkeypatch):
    # 404, oversized, and content-type mismatches are deterministic — the
    # retry loop must short-circuit so we don't spend three round-trips on
    # something that won't get better.
    monkeypatch.setattr(art_overrides, "_FETCH_RETRY_DELAYS_S", [0.0, 0.0])
    session = _SequenceSession([_Resp(status=404)])
    with pytest.raises(art_overrides.OverrideError) as exc:
        _run(art_overrides.set(
            "Artist", "Album",
            "https://i.discogs.com/missing.jpg",
            "discogs-master", session=session,
        ))
    assert exc.value.status == 502
    assert session.calls == 1  # no retry attempted


def test_set_rejects_non_allowlisted_url():
    session = _Session(_Resp())
    with pytest.raises(art_overrides.OverrideError) as exc:
        _run(art_overrides.set(
            "Artist", "Album", "https://evil.example.com/x.jpg",
            "user", session=session,
        ))
    assert exc.value.status == 400


def test_set_rejects_non_image_content_type():
    session = _Session(_Resp(ctype="text/html"))
    with pytest.raises(art_overrides.OverrideError) as exc:
        _run(art_overrides.set(
            "Artist", "Album",
            "https://i.discogs.com/x.html",
            "user", session=session,
        ))
    assert exc.value.status == 502


def test_set_rejects_non_200_status():
    session = _Session(_Resp(status=404))
    with pytest.raises(art_overrides.OverrideError) as exc:
        _run(art_overrides.set(
            "A", "B", "https://i.discogs.com/x.jpg",
            "user", session=session,
        ))
    assert exc.value.status == 502


def test_set_failure_does_not_persist():
    session = _Session(_Resp(ctype="text/plain"))
    with pytest.raises(art_overrides.OverrideError):
        _run(art_overrides.set(
            "A", "B", "https://i.discogs.com/x.jpg",
            "user", session=session,
        ))
    assert art_overrides.get("A", "B") is None
    assert not (art_overrides.INDEX_PATH).exists() or json.loads(
        art_overrides.INDEX_PATH.read_text(),
    ) == {}


def test_clear_removes_index_and_file():
    session = _Session(_Resp())
    _run(art_overrides.set(
        "A", "B", "https://i.discogs.com/x.jpg",
        "user", session=session,
    ))
    assert art_overrides.get("A", "B") is not None
    art_overrides.clear("A", "B")
    assert art_overrides.get("A", "B") is None
    assert art_overrides.read_bytes("A", "B") is None


def test_read_bytes_returns_none_when_local_file_missing():
    session = _Session(_Resp())
    ov = _run(art_overrides.set(
        "A", "B", "https://i.discogs.com/x.jpg",
        "user", session=session,
    ))
    Path(ov.local_path).unlink()
    assert art_overrides.read_bytes("A", "B") is None


def test_key_normalization_matches_artcache():
    from nowplaying import artcache
    a = art_overrides.key_for("The Beatles", "Rubber Soul")
    b = artcache.key_for("The Beatles", "Rubber Soul")
    assert a == b
