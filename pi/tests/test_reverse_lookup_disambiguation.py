"""Tests for the LLM-assisted reverse-lookup disambiguation helper
(``_maybe_llm_disambiguate_reverse_lookup``) in
``pi/nowplaying/orchestrator/_llm_hooks.py``.

The helper is the orchestrator-side glue that consults the LLM
``judge_reverse_lookup`` hook when the catalog's reverse-lookup attached
``alternate_releases`` (heuristic-tied candidates within ~20 points of
the winner). When the LLM picks an alternate, the helper mutates the
``result`` dict in place to swap winner-derived fields. See
docs/features/llm-assisted-reverse-lookup/.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nowplaying.orchestrator._class import Orchestrator
from nowplaying.orchestrator.state import State


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_state() -> State:
    s = State()
    s.sonos_source = "vinyl"
    s.last_vinyl = {
        "release_id": 4042258,
        "track_position": "A2",
        "side": "A",
        "title": "Something",
        "album": "Abbey Road",
        "artist": "The Beatles",
    }
    s.last_vinyl_confidence_set_at = None
    return s


def _make_orchestrator(state: State, *, llm_enabled: bool = True) -> Orchestrator:
    bcast = AsyncMock()
    bcast.publish = AsyncMock()
    llm = MagicMock(enabled=llm_enabled)
    return Orchestrator(
        state=state,
        bcast=bcast,
        sonos_coord=None,
        stop=asyncio.Event(),
        llm=llm,
        fingerprint_enabled=False,
    )


def _result_with_alternates(
    winner_rid: int = 4042258,
    artist: str = "The Beatles",
    title: str = "Something",
    alternates: list[dict] | None = None,
) -> dict:
    return {
        "match_method": "shazam",
        "release_id": winner_rid,
        "artist": artist,
        "title": title,
        "album": "Abbey Road",
        "year": 2012,
        "track_position": "A2",
        "match_score": 100,
        "alternate_releases": alternates or [
            {
                "release_id": 28859305,
                "album": "1962-1966",
                "year": 2023,
                "track_position": "A3",
                "track_title": "Something",
                "score": 85,
            },
        ],
    }


# ── No-op cases ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_alternates_no_consult():
    """When no alternates are attached, the helper returns immediately
    without consulting the LLM."""
    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock()
    result = {"release_id": 4042258, "artist": "X", "title": "Y"}

    await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    orch.llm.judge_reverse_lookup.assert_not_awaited()
    assert result["release_id"] == 4042258  # untouched


@pytest.mark.asyncio
async def test_llm_disabled_no_consult():
    """When the LLM is disabled, the helper returns immediately even if
    alternates are attached."""
    state = _make_state()
    orch = _make_orchestrator(state, llm_enabled=False)
    orch.llm.judge_reverse_lookup = AsyncMock()
    result = _result_with_alternates()

    await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    orch.llm.judge_reverse_lookup.assert_not_awaited()
    assert result["release_id"] == 4042258


@pytest.mark.asyncio
async def test_winner_rid_missing_no_consult():
    """Defensive: if the result has no release_id, no disambiguation
    possible."""
    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock()
    result = _result_with_alternates(winner_rid=None)
    result["release_id"] = None

    await orch._maybe_llm_disambiguate_reverse_lookup(result, state)
    orch.llm.judge_reverse_lookup.assert_not_awaited()


# ── Happy-path swap ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_picks_alternate_swaps_result_in_place():
    """When the LLM picks an alternate's release_id, the helper looks
    up the new release in the catalog and mutates result fields:
    release_id, album, track_position, etc."""
    from nowplaying.llm.release import ReleasePick

    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock(
        return_value=ReleasePick(
            release_id=28859305, reason="recent history shows fresh side",
        ),
    )
    result = _result_with_alternates()

    new_release = {
        "id": 28859305,
        "title": "1962-1966",
        "year": 2023,
        "label": "Apple",
        "catno": "0602445599714",
        "art_path": "data/art/28859305.jpg",
        "tracks": [
            {
                "position": "A3", "side": "A",
                "title": "Something", "duration_seconds": 182,
            },
        ],
    }
    with patch(
        "nowplaying.discogs.catalog.get_release", return_value=new_release,
    ):
        await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    assert result["release_id"] == 28859305
    assert result["album"] == "1962-1966"
    assert result["year"] == 2023
    assert result["label"] == "Apple"
    assert result["catno"] == "0602445599714"
    assert result["track_position"] == "A3"
    assert result["title"] == "Something"
    assert len(result["tracklist"]) == 1


@pytest.mark.asyncio
async def test_llm_confirms_winner_no_swap():
    """LLM picking the current winner is a confirmation, not a swap —
    fields stay untouched, log line emitted."""
    from nowplaying.llm.release import ReleasePick

    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock(
        return_value=ReleasePick(release_id=4042258, reason="locked album confirmed"),
    )
    result = _result_with_alternates()
    original_keys = {k: result[k] for k in ("release_id", "album", "track_position")}

    with patch("nowplaying.discogs.catalog.get_release") as fake_get:
        await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    # No catalog lookup needed when winner is confirmed.
    fake_get.assert_not_called()
    for k, v in original_keys.items():
        assert result[k] == v


# ── Failure modes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_hallucinates_unknown_release_id_keeps_heuristic():
    """LLM returns a release_id NOT in {winner_rid} ∪ {alt_rids} →
    log warning, no swap."""
    from nowplaying.llm.release import ReleasePick

    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock(
        return_value=ReleasePick(release_id=99999999, reason="hallucination"),
    )
    result = _result_with_alternates()

    with patch("nowplaying.discogs.catalog.get_release") as fake_get:
        await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    fake_get.assert_not_called()
    assert result["release_id"] == 4042258


@pytest.mark.asyncio
async def test_use_heuristic_sentinel_skips_swap():
    """LLM returning USE_HEURISTIC means "don't override" — no swap."""
    from nowplaying.llm import USE_HEURISTIC

    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock(return_value=USE_HEURISTIC)
    result = _result_with_alternates()

    with patch("nowplaying.discogs.catalog.get_release") as fake_get:
        await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    fake_get.assert_not_called()
    assert result["release_id"] == 4042258


@pytest.mark.asyncio
async def test_picked_release_not_in_catalog_keeps_heuristic():
    """LLM picks a valid alternate-listed release_id, but the catalog
    lookup returns None (database race / corruption) → keep heuristic
    winner."""
    from nowplaying.llm.release import ReleasePick

    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock(
        return_value=ReleasePick(release_id=28859305, reason="flip"),
    )
    result = _result_with_alternates()

    with patch("nowplaying.discogs.catalog.get_release", return_value=None):
        await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    assert result["release_id"] == 4042258  # original winner preserved


@pytest.mark.asyncio
async def test_llm_raises_keeps_heuristic():
    """If the LLM call raises (network, parse error, etc.), the helper
    swallows the exception and keeps the heuristic winner."""
    state = _make_state()
    orch = _make_orchestrator(state)
    orch.llm.judge_reverse_lookup = AsyncMock(side_effect=RuntimeError("API down"))
    result = _result_with_alternates()

    await orch._maybe_llm_disambiguate_reverse_lookup(result, state)

    assert result["release_id"] == 4042258
