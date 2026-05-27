"""Stream behavior of the candidate aggregator: dedup, partial results,
'current' always emitted first."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nowplaying import art_picker  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _collect(gen):
    out = []
    async for c in gen:
        out.append(c)
    return out


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Stub out art_overrides.get so we don't hit a real index file.
    monkeypatch.setattr(
        art_picker.art_overrides, "get",
        lambda artist, album: None,
    )
    # Stub out the CAA URL-existence HEAD probe. Real picker behavior
    # filters out MBIDs without uploaded art; in unit tests we assume
    # every proposed URL exists so the dedup/ordering/source logic can
    # be exercised without the network.
    async def _exists(_session, _url, timeout_s=2.0):
        return True
    monkeypatch.setattr(art_picker, "_caa_url_exists", _exists)


def test_current_emitted_first_even_when_other_sources_fail():
    async def fail(*_a, **_kw):
        return None

    async def _no_master(_s, _rid):
        return None

    async def _no_release(_s, _rid):
        return []

    with patch.object(art_picker.dimages, "resolve_master_id", AsyncMock(return_value=None)), \
         patch.object(art_picker.dimages, "fetch_release_images", AsyncMock(return_value=[])), \
         patch.object(art_picker.coverart, "fetch_release_mbid", AsyncMock(return_value=None)):
        out = _run(_collect(art_picker.fetch_candidates(
            "The Beatles", "Abbey Road", 12345, session=None,
        )))
    assert out
    assert out[0]["source"] == "current"
    assert out[0]["release_id"] == 12345


def test_dedup_across_sources():
    same_url = "https://i.discogs.com/dup.jpg"

    with patch.object(art_picker.dimages, "resolve_master_id", AsyncMock(return_value=99)), \
         patch.object(art_picker.dimages, "fetch_master_images", AsyncMock(return_value=[
             {"url": same_url, "type": "primary"},
         ])), \
         patch.object(art_picker.dimages, "fetch_release_images", AsyncMock(return_value=[
             {"url": same_url, "type": "primary"},
         ])), \
         patch.object(art_picker.coverart, "fetch_release_mbid", AsyncMock(return_value=None)):
        out = _run(_collect(art_picker.fetch_candidates(
            "A", "B", 1, session=None,
        )))
    urls = [c["url"] for c in out]
    # same_url appears at most once.
    assert urls.count(same_url) == 1


def test_caa_emitted_when_mbid_resolves():
    """Single-release fallback path: when the multi-release search misses,
    `_emit_caa` falls back to the legacy single-hit `fetch_release_mbid`."""
    with patch.object(art_picker.dimages, "resolve_master_id", AsyncMock(return_value=None)), \
         patch.object(art_picker.dimages, "fetch_release_images", AsyncMock(return_value=[])), \
         patch.object(art_picker.coverart, "search_release_candidates", AsyncMock(return_value=[])), \
         patch.object(art_picker.coverart, "fetch_release_mbid", AsyncMock(return_value=("abc-mbid", None))):
        out = _run(_collect(art_picker.fetch_candidates(
            "A", "B", 1, session=None,
        )))
    caa = [c for c in out if c["source"] == "caa"]
    assert len(caa) == 1
    assert "abc-mbid" in caa[0]["url"]
    assert "front-1200" in caa[0]["url"]


def test_caa_emits_multiple_when_search_returns_multiple():
    """Primary path: when search_release_candidates returns multiple MB
    releases, each gets its own CAA tile (release-level + release-group)."""
    with patch.object(art_picker.dimages, "resolve_master_id", AsyncMock(return_value=None)), \
         patch.object(art_picker.dimages, "fetch_release_images", AsyncMock(return_value=[])), \
         patch.object(art_picker.coverart, "search_release_candidates", AsyncMock(return_value=[
             ("rel-1", "rg-1"),
             ("rel-2", "rg-2"),
             ("rel-3", None),
         ])):
        out = _run(_collect(art_picker.fetch_candidates(
            "A", "B", 1, session=None,
        )))
    caa = [c for c in out if c["source"] == "caa"]
    urls = [c["url"] for c in caa]
    # 3 release-level + 2 release-group (rel-3 has no RG).
    assert any("release/rel-1/front-1200" in u for u in urls)
    assert any("release/rel-2/front-1200" in u for u in urls)
    assert any("release/rel-3/front-1200" in u for u in urls)
    assert any("release-group/rg-1/front-1200" in u for u in urls)
    assert any("release-group/rg-2/front-1200" in u for u in urls)
    assert len(caa) == 5


def test_caa_filters_out_mbids_without_uploaded_art(monkeypatch):
    """Some MBIDs in MusicBrainz don't have a front-cover upload in CAA.
    Emitting their /front-1200 URLs gives the kiosk's picker dead-link
    tiles (404). The pre-flight HEAD probe must filter those out so only
    candidates that actually resolve to an image hit the SSE stream.
    """
    # Override the autouse "all URLs exist" stub: rel-1 has art, rel-2
    # doesn't, rel-3 has art. The release-group URLs all exist.
    def _selective_exists_factory():
        async def _exists(_session, url, timeout_s=2.0):
            return "/release/rel-2/" not in url
        return _exists
    monkeypatch.setattr(
        art_picker, "_caa_url_exists", _selective_exists_factory(),
    )
    with patch.object(art_picker.dimages, "resolve_master_id", AsyncMock(return_value=None)), \
         patch.object(art_picker.dimages, "fetch_release_images", AsyncMock(return_value=[])), \
         patch.object(art_picker.coverart, "search_release_candidates", AsyncMock(return_value=[
             ("rel-1", "rg-1"),
             ("rel-2", "rg-2"),
             ("rel-3", None),
         ])):
        out = _run(_collect(art_picker.fetch_candidates(
            "A", "B", 1, session=None,
        )))
    caa_urls = [c["url"] for c in out if c["source"] == "caa"]
    # rel-1 + rel-3 release-level survive; rel-2 is filtered out.
    assert any("release/rel-1/front-1200" in u for u in caa_urls)
    assert not any("release/rel-2/front-1200" in u for u in caa_urls)
    assert any("release/rel-3/front-1200" in u for u in caa_urls)
