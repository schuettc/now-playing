"""Tests for the artist+album (by-name) art-picker code paths.

Covers the new plumbing that lets the picker work on streaming /
AirPlay tracks that don't match a record in the user's Discogs
collection. See `docs/features/art-picker-without-discogs/`.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

import pytest  # noqa: E402

from nowplaying import art_overrides, art_picker  # noqa: E402


# ---- art_picker.fetch_candidates with release_id=None --------------------


@pytest.mark.asyncio
async def test_fetch_candidates_no_release_id_skips_discogs():
    """When `release_id` is None, only CAA + current tasks are scheduled.
    The Discogs master and Discogs release emitters never fire."""

    async def _fake_caa(queue, artist, album, **_kw):
        await queue.put({
            "url": "https://coverartarchive.org/release-group/abc/front.jpg",
            "source": "caa",
            "label": "MusicBrainz",
        })

    discogs_master_called = False
    discogs_release_called = False

    async def _fake_discogs_master(*_args, **_kwargs):
        nonlocal discogs_master_called
        discogs_master_called = True

    async def _fake_discogs_release(*_args, **_kwargs):
        nonlocal discogs_release_called
        discogs_release_called = True

    with patch.object(art_picker, "_emit_caa", _fake_caa), \
         patch.object(art_picker, "_emit_discogs_master", _fake_discogs_master), \
         patch.object(art_picker, "_emit_discogs_release", _fake_discogs_release), \
         patch.object(art_picker.art_overrides, "get", return_value=None):
        results = []
        async for cand in art_picker.fetch_candidates(
            artist="Hum",
            album="Downward Is Heavenward",
            release_id=None,
            session=None,  # not used by the fakes
            current_url="/art-cache/abc123",
        ):
            results.append(cand)

    # Current candidate + CAA candidate, no Discogs.
    sources = [c.get("source") for c in results]
    assert "current" in sources
    assert "caa" in sources
    assert not discogs_master_called, "Discogs master should not fire without release_id"
    assert not discogs_release_called, "Discogs release should not fire without release_id"
    # Without an override, the "Current" tile falls back to the kiosk's
    # currently displayed art_url (typically the streaming-service URL),
    # NOT /art-by-name — that route only resolves once an override is saved.
    current = [c for c in results if c.get("source") == "current"][0]
    assert current["url"] == "/art-cache/abc123"


@pytest.mark.asyncio
async def test_fetch_candidates_no_rid_with_override_uses_art_by_name():
    """When an override exists for (artist, album), the 'Current' tile
    points at /art-by-name so saving "Keep current" stays a no-op."""
    from nowplaying.art_overrides import Override

    fake_ov = Override(
        key="abc",
        url="https://example/x.jpg",
        source="caa",
        picked_at="2026-05-14T20:00:00Z",
        local_path="/tmp/x.jpg",
        content_type="image/jpeg",
        picked_at_epoch=0,
    )

    async def _fake_caa(*_a, **_kw):
        pass

    async def _fake_discogs(*_a, **_kw):
        pass

    with patch.object(art_picker, "_emit_caa", _fake_caa), \
         patch.object(art_picker, "_emit_discogs_master", _fake_discogs), \
         patch.object(art_picker, "_emit_discogs_release", _fake_discogs), \
         patch.object(art_picker.art_overrides, "get", return_value=fake_ov):
        results = []
        async for cand in art_picker.fetch_candidates(
            artist="Hum", album="You & Me", release_id=None,
            session=None, current_url="/art-cache/abc",
        ):
            results.append(cand)

    current = [c for c in results if c.get("source") == "current"][0]
    assert current["url"].startswith("/art-by-name?"), current["url"]
    assert "artist=Hum" in current["url"]


@pytest.mark.asyncio
async def test_fetch_candidates_with_release_id_includes_discogs():
    """Regression: when `release_id` is set, Discogs sources still fire.
    Vinyl path must be unchanged."""

    async def _fake_caa(*_args, **_kwargs):
        pass

    discogs_master_called = False

    async def _fake_discogs_master(*_args, **_kwargs):
        nonlocal discogs_master_called
        discogs_master_called = True

    async def _fake_discogs_release(*_args, **_kwargs):
        pass

    with patch.object(art_picker, "_emit_caa", _fake_caa), \
         patch.object(art_picker, "_emit_discogs_master", _fake_discogs_master), \
         patch.object(art_picker, "_emit_discogs_release", _fake_discogs_release), \
         patch.object(art_picker.art_overrides, "get", return_value=None):
        results = []
        async for cand in art_picker.fetch_candidates(
            artist="Hum",
            album="Downward Is Heavenward",
            release_id=29155441,
            session=None,
        ):
            results.append(cand)

    assert discogs_master_called, "Discogs master should fire when release_id is set"
    current = [c for c in results if c.get("source") == "current"][0]
    # Current candidate uses /art/<id> URL when release_id is set.
    assert current["url"].startswith("/art/29155441"), current["url"]


# ---- art_overrides in-memory cache ---------------------------------------


def test_art_overrides_cache_repopulates_after_invalidate(tmp_path, monkeypatch):
    """After set/clear invalidates the cache, the next read reflects disk
    state. Demonstrates that the cache doesn't go stale."""
    monkeypatch.setattr(art_overrides, "OVERRIDES_DIR", tmp_path)
    monkeypatch.setattr(art_overrides, "INDEX_PATH", tmp_path / "index.json")
    art_overrides._invalidate_index_cache()

    # No index file yet → cache caches the empty dict.
    assert art_overrides._load_index() == {}
    # Subsequent reads use the cache (no re-parse of disk).
    assert art_overrides._load_index() == {}

    # Simulate a disk write by another writer (or our own set/clear).
    key = art_overrides.key_for("Hum", "Downward Is Heavenward")
    assert key is not None
    (tmp_path / "index.json").write_text(json.dumps({
        key: {
            "url": "https://example/x.jpg",
            "source": "caa",
            "picked_at": "2026-05-14T20:00:00Z",
            "picked_at_epoch": 1747252800,
            "local_path": str(tmp_path / "x.jpg"),
            "content_type": "image/jpeg",
        },
    }))
    # Without invalidation the cache still returns empty — that's
    # expected; cache invalidation is the contract.
    assert art_overrides._load_index() == {}

    # Invalidate → next read picks up the new content.
    art_overrides._invalidate_index_cache()
    loaded = art_overrides._load_index()
    assert key in loaded


def test_art_overrides_get_after_clear_returns_none(tmp_path, monkeypatch):
    """End-to-end: clear() must invalidate the cache so subsequent
    `get()` calls don't return the cleared override."""
    monkeypatch.setattr(art_overrides, "OVERRIDES_DIR", tmp_path)
    monkeypatch.setattr(art_overrides, "INDEX_PATH", tmp_path / "index.json")
    art_overrides._invalidate_index_cache()

    # Seed an override on disk manually (the canonical set() requires
    # an HTTP session; this fixture is testing cache behaviour, not
    # the HTTP fetch path). Use the canonical key derived from
    # (artist, album) so subsequent get() calls find it.
    art_path = tmp_path / "img.jpg"
    art_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpg")
    key = art_overrides.key_for("Test Artist", "Test Album")
    assert key is not None
    (tmp_path / "index.json").write_text(json.dumps({
        key: {
            "url": "https://example/x.jpg",
            "source": "caa",
            "picked_at": "2026-05-14T20:00:00Z",
            "picked_at_epoch": 1747252800,
            "local_path": str(art_path),
            "content_type": "image/jpeg",
        },
    }))
    art_overrides._invalidate_index_cache()

    ov = art_overrides.get("Test Artist", "Test Album")
    assert ov is not None
    assert ov.url == "https://example/x.jpg"

    removed = art_overrides.clear("Test Artist", "Test Album")
    assert removed is True
    # After clear, the next get() should NOT find the override.
    assert art_overrides.get("Test Artist", "Test Album") is None
