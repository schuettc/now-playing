"""Tests for the Wikipedia album-context blurb fetcher.

Regression coverage for the Slumber Party / Plastikman "Musik" collision:
without artist verification, the generic `Musik (album)` disambiguator
returns Plastikman's page even though Slumber Party is playing.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import wiki  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "WIKI_DIR", tmp_path / "wiki")
    wiki._negative_cache.clear()
    yield


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _payload_matches_artist
# ---------------------------------------------------------------------------

def test_verifier_matches_on_description():
    payload = {
        "description": "1996 studio album by Slumber Party",
        "extract": "Musik is the third studio album...",
    }
    assert wiki._payload_matches_artist(payload, "Slumber Party") is True


def test_verifier_rejects_wrong_artist():
    payload = {
        "description": "1994 studio album by Richie Hawtin",
        "extract": "Musik is the third studio album by Canadian "
                   "electronic music producer Richie Hawtin...",
    }
    assert wiki._payload_matches_artist(payload, "Slumber Party") is False


def test_verifier_falls_back_to_extract():
    payload = {
        "description": "",
        "extract": "Siamese Dream is the second studio album by the "
                   "American alternative rock band The Smashing Pumpkins...",
    }
    assert wiki._payload_matches_artist(
        payload, "The Smashing Pumpkins",
    ) is True


def test_verifier_multi_word_artist_partial_match():
    """Multi-word artists match on any non-stopword token."""
    payload = {"description": "Album by Pumpkins", "extract": ""}
    assert wiki._payload_matches_artist(
        payload, "The Smashing Pumpkins",
    ) is True


def test_verifier_stopwords_dont_carry_match():
    """`The` alone should not be enough to accept any page."""
    payload = {
        "description": "1994 studio album by Richie Hawtin",
        "extract": "...",
    }
    # Artist tokens after stopword filter: ["smashing", "pumpkins"]
    # Neither appears in the Plastikman payload → reject.
    assert wiki._payload_matches_artist(
        payload, "The Smashing Pumpkins",
    ) is False


def test_verifier_fails_open_for_all_stopwords_artist():
    """Artists like "The The" tokenize to nothing — fail open."""
    payload = {
        "description": "1981 album by some other band",
        "extract": "...",
    }
    assert wiki._payload_matches_artist(payload, "The The") is True


def test_verifier_fails_open_for_missing_artist():
    assert wiki._payload_matches_artist({"description": "x"}, "") is True


def test_verifier_unicode_artist_tokenizes_intact():
    """Accented characters must survive tokenization — splitting
    "Rós" at the ó would leave a stray "s" token that false-matches
    any apostrophe-s in prose."""
    assert wiki._artist_tokens("Sigur Rós") == ["sigur", "rós"]


def test_verifier_unicode_artist_match_and_reject():
    right = {
        "description": "1999 studio album by Sigur Rós",
        "extract": "Ágætis byrjun is the second studio album by Sigur Rós...",
    }
    wrong = {
        "description": "1991 album by Some Other Band",
        "extract": "The band's debut produced no hits.",
    }
    assert wiki._payload_matches_artist(right, "Sigur Rós") is True
    # The single-letter "s" from "Rós" must NOT be in the token set,
    # so the apostrophe-s in "band's" should not carry the match.
    assert wiki._payload_matches_artist(wrong, "Sigur Rós") is False


def test_verifier_punctuation_artist_drops_single_letters():
    """`Jane's Addiction` must not match on the bare `s` token."""
    assert wiki._artist_tokens("Jane's Addiction") == ["jane", "addiction"]


def test_verifier_single_letter_band_fails_open():
    """Artists like `X` or `M` tokenize to nothing after the
    len>1 filter and should fail open rather than over-reject."""
    payload = {
        "description": "1980 album by some band",
        "extract": "Their debut.",
    }
    assert wiki._payload_matches_artist(payload, "X") is True
    assert wiki._payload_matches_artist(payload, "M") is True


def test_verifier_short_artist_token_word_boundary():
    """Short artist names like 'Me' or 'Low' must not false-positive on
    substring matches inside ordinary prose like 'someone' or 'below'."""
    payload = {
        "description": "1991 studio album by Some Other Band",
        "extract": "Below the surface, someone used a beautiful synth.",
    }
    # "me" appears inside "someone", "us" inside "used", "low" inside
    # "below" — all substring hits that must NOT count as artist matches.
    assert wiki._payload_matches_artist(payload, "Me") is False
    assert wiki._payload_matches_artist(payload, "Us") is False
    assert wiki._payload_matches_artist(payload, "Low") is False


def test_verifier_short_artist_token_matches_whole_word():
    """The verifier must still accept genuine whole-word matches for
    short artist names."""
    payload = {
        "description": "1994 album by Low",
        "extract": "Their debut album.",
    }
    assert wiki._payload_matches_artist(payload, "Low") is True


def test_verifier_extract_only_checks_head():
    """Artist token deep in `extract` (past ~300 chars) shouldn't match."""
    long_prefix = "x " * 400  # 800 chars of filler
    payload = {
        "description": "",
        "extract": long_prefix + "Slumber Party",
    }
    assert wiki._payload_matches_artist(
        payload, "Slumber Party",
    ) is False


# ---------------------------------------------------------------------------
# cached_summary / store_summary — v2 cache versioning
# ---------------------------------------------------------------------------

def test_cache_v1_treated_as_miss(tmp_path):
    """Pre-versioning entries shouldn't be served — they may be wrong."""
    p = wiki.WIKI_DIR
    p.mkdir(parents=True, exist_ok=True)
    (p / "42.json").write_text(json.dumps({
        "summary": "old poisoned blurb",
        "url": "https://en.wikipedia.org/wiki/Musik_(album)",
        "title": "Musik (album)",
    }))
    assert wiki.cached_summary(42) is None


def test_cache_v2_returned():
    wiki.store_summary(42, {
        "summary": "fresh verified blurb",
        "url": "https://example.com",
        "title": "Some Album",
    })
    got = wiki.cached_summary(42)
    assert got is not None
    assert got["summary"] == "fresh verified blurb"
    assert got["v"] == wiki.CACHE_VERSION


def test_cache_corrupt_returns_none():
    p = wiki.WIKI_DIR
    p.mkdir(parents=True, exist_ok=True)
    (p / "99.json").write_text("not json{")
    assert wiki.cached_summary(99) is None


# ---------------------------------------------------------------------------
# fetch_summary end-to-end with mocked HTTP
# ---------------------------------------------------------------------------

class _FakeResponse:  # skylos: ignore SKY-Q702 — test fake; low cohesion is intentional. Mimics aiohttp's ClientResponse interface across async context-manager + sync attribute + async json() surfaces, which are genuinely disconnected method groups.
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


def _session_with_responses(get_map):
    """Build a fake ClientSession whose `.get(url, ...)` returns the next
    `_FakeResponse` in the queue for that URL prefix.

    `get_map` is a dict of {url-substring: [_FakeResponse, ...]}. The first
    matching prefix wins. Unmatched URLs raise.
    """
    queues = {k: list(v) for k, v in get_map.items()}

    def _get(url, **_kwargs):
        for key, queue in queues.items():
            if key in url and queue:
                return queue.pop(0)
        raise AssertionError(f"unexpected GET {url!r}")

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    return session


def test_wrong_artist_rejected_then_cascade_continues():
    """The Slumber Party regression: every artist-less candidate that
    returns Plastikman's page must be rejected by the verifier."""
    plastikman = {
        "description": "1994 studio album by Richie Hawtin",
        "extract": "Musik is the third studio album by Canadian "
                   "electronic music producer Richie Hawtin...",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Musik_(album)"}},
        "title": "Musik (album)",
    }
    session = _session_with_responses({
        # Strict artist-bound titles 404
        "Musik%20%28Slumber%20Party%20album%29": [_FakeResponse(404)],
        "Musik%20%28Slumber%20Party%29": [_FakeResponse(404)],
        # Generic-disambig and bare title both 200 with Plastikman →
        # verifier must reject both. Two responses for `Musik (album)`
        # because the cascade hits it once, and opensearch's fallback
        # path may hit it again.
        "Musik%20%28album%29": [
            _FakeResponse(200, plastikman),
            _FakeResponse(200, plastikman),
        ],
        "Musik": [_FakeResponse(200, plastikman)],
        # Opensearch returns Plastikman's title — also rejected.
        "w/api.php": [_FakeResponse(200, [
            "Slumber Party Musik album", ["Musik (album)"], [""], [""],
        ])],
    })
    with patch.object(wiki.aiohttp, "ClientSession", return_value=_AsyncCM(session)):
        result = _run(wiki.fetch_summary("Slumber Party", "Musik"))
    assert result is None, (
        "wrong-artist Plastikman page must be rejected, "
        f"got {result!r}"
    )


def test_artist_less_disambiguator_accepted_when_artist_matches():
    """Regression: Wikipedia pages with no disambiguator (e.g. Siamese
    Dream — unique title, no other album with that name) must still be
    findable. The strict artist-bound titles 404; the bare `{album}`
    candidate returns a verified-correct page; we accept it."""
    sp = {
        "description": "1993 studio album by The Smashing Pumpkins",
        "extract": "Siamese Dream is the second studio album by the "
                   "American alternative rock band The Smashing Pumpkins...",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Siamese_Dream"}},
        "title": "Siamese Dream",
    }
    session = _session_with_responses({
        # Strict artist-bound titles don't exist on Wikipedia
        "Siamese%20Dream%20%28The%20Smashing%20Pumpkins%20album%29":
            [_FakeResponse(404)],
        "Siamese%20Dream%20%28The%20Smashing%20Pumpkins%29":
            [_FakeResponse(404)],
        # Generic `(album)` form also 404 (Wikipedia doesn't use it
        # for unique titles)
        "Siamese%20Dream%20%28album%29": [_FakeResponse(404)],
        # Bare title returns the correct page; verifier accepts.
        "Siamese%20Dream": [_FakeResponse(200, sp)],
    })
    with patch.object(wiki.aiohttp, "ClientSession", return_value=_AsyncCM(session)):
        result = _run(wiki.fetch_summary(
            "The Smashing Pumpkins", "Siamese Dream",
        ))
    assert result is not None, "bare-title Wikipedia page must be findable"
    assert "Smashing Pumpkins" in result["summary"]


def test_right_artist_accepted_on_strict_title():
    sp = {
        "description": "1993 studio album by The Smashing Pumpkins",
        "extract": "Siamese Dream is the second studio album by the "
                   "American alternative rock band The Smashing Pumpkins...",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Siamese_Dream"}},
        "title": "Siamese Dream",
    }
    session = _session_with_responses({
        "Siamese%20Dream%20%28The%20Smashing%20Pumpkins%20album%29":
            [_FakeResponse(200, sp)],
    })
    with patch.object(wiki.aiohttp, "ClientSession", return_value=_AsyncCM(session)):
        result = _run(wiki.fetch_summary(
            "The Smashing Pumpkins", "Siamese Dream",
        ))
    assert result is not None
    assert result["title"] == "Siamese Dream"
    assert "Smashing Pumpkins" in result["summary"]


class _AsyncCM:
    """Wrap a sync object as an async-context-manager (for the
    `async with aiohttp.ClientSession(...) as session:` pattern)."""
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *exc):
        return False
