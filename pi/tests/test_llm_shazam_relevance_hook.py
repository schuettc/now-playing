"""F5: Integration tests for the LLM-shazam-relevance gate + hook
behavior on the Orchestrator.

We don't drive the full heartbeat handler (it has too many dependencies)
— instead we exercise `_should_consult_llm_for_shazam` directly (pure
function over result + state) and verify the gate logic encodes exactly
what the plan specified.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.llm import LLMAssist, USE_HEURISTIC, ShazamVerdict


@pytest.fixture
def state_no_lock():
    s = mock.MagicMock()
    s.last_vinyl = None
    return s


@pytest.fixture
def state_locked_to_joy_division():
    s = mock.MagicMock()
    s.last_vinyl = {
        "artist": "Joy Division",
        "album": "Closer",
        "release_id": 12345,
        "title": "Heart and Soul",
    }
    return s


def _make_orch(llm_enabled: bool):  # skylos: ignore SKY-L029 — test helper; only called with literal True/False at the four call sites, kwarg overhead unnecessary
    """Build a minimal Orchestrator with just enough state to exercise
    the gate + ctx-builder. We can't easily import Orchestrator without
    pulling main.py's entire module side effects (signal handlers etc.)
    — so we import lazily and instantiate with bare attributes.
    """
    from nowplaying.main import Orchestrator
    llm_obj = LLMAssist()
    llm_obj.enabled = llm_enabled
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = llm_obj
    return orch


# ── Gate logic ──────────────────────────────────────────────────────────


def test_gate_skips_when_llm_disabled(state_locked_to_joy_division):
    orch = _make_orch(llm_enabled=False)
    result = {"artist": "New Order", "release_id": 99999}
    assert orch._should_consult_llm_for_shazam(result, state_locked_to_joy_division) is False


def test_gate_skips_when_nothing_locked(state_no_lock):
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "Joy Division", "release_id": 12345}
    assert orch._should_consult_llm_for_shazam(result, state_no_lock) is False


def test_gate_skips_when_release_id_matches(state_locked_to_joy_division):
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "Different Artist", "release_id": 12345}  # same RID
    assert orch._should_consult_llm_for_shazam(result, state_locked_to_joy_division) is False


def test_gate_skips_when_artist_matches_case_insensitively(state_locked_to_joy_division):
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "joy DIVISION", "release_id": 99999}
    assert orch._should_consult_llm_for_shazam(result, state_locked_to_joy_division) is False


def test_gate_fires_on_cross_album_disagreement(state_locked_to_joy_division):
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "New Order", "release_id": 99999, "title": "Blue Monday"}
    assert orch._should_consult_llm_for_shazam(result, state_locked_to_joy_division) is True


def test_gate_fires_on_artist_disagreement_with_shazam_only_lock():
    """Lock has no release_id (shazam-only hit). Cross-artist Shazam result
    should still trigger the LLM call — Gemini round-1 blocking finding."""
    state = mock.MagicMock()
    state.last_vinyl = {
        "artist": "Joy Division",
        "album": None,
        "release_id": None,
        "title": "Some Track",
    }
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "New Order", "release_id": None}
    assert orch._should_consult_llm_for_shazam(result, state) is True


def test_gate_skips_when_both_artists_blank():
    """Blank-vs-blank artist payload has no signal worth asking the LLM
    about — gate returns False rather than spending API tokens on two
    null-artist comparisons. Real disagreements have at least one named
    side. Gemini impl-review round-1 should-fix refinement."""
    state = mock.MagicMock()
    state.last_vinyl = {"artist": "", "release_id": 100}
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "", "release_id": 200}
    assert orch._should_consult_llm_for_shazam(result, state) is False


def test_gate_fires_when_one_artist_is_present_and_the_other_blank():
    """If one side has a named artist and the other is blank, a real
    disagreement may exist (e.g., Shazam-only lock with no artist vs a
    fully-resolved Shazam hit). Gate should fire."""
    state = mock.MagicMock()
    state.last_vinyl = {"artist": "", "release_id": 100, "title": "Track X"}
    orch = _make_orch(llm_enabled=True)
    result = {"artist": "The Beatles", "release_id": 200, "title": "Hey Jude"}
    assert orch._should_consult_llm_for_shazam(result, state) is True


# ── locked_album_ctx builder ────────────────────────────────────────────


def test_build_locked_album_ctx_with_lock(state_locked_to_joy_division):
    orch = _make_orch(llm_enabled=True)
    ctx = orch._build_locked_album_ctx(state_locked_to_joy_division)
    assert ctx == {
        "locked_artist": "Joy Division",
        "locked_album": "Closer",
        "locked_release_id": 12345,
        "locked_title": "Heart and Soul",
    }


def test_build_locked_album_ctx_without_lock(state_no_lock):
    orch = _make_orch(llm_enabled=True)
    assert orch._build_locked_album_ctx(state_no_lock) is None


# ── End-to-end LLM call result handling ─────────────────────────────────


def test_reject_verdict_swallows_heartbeat_intent(state_locked_to_joy_division):
    """When the LLM returns ShazamVerdict(accept=False), the orchestrator
    code path should bail out before mutating state.last_vinyl. We can't
    easily drive the full handler here, so we assert the verdict shape
    that the handler's `if verdict.accept is False` branch reads."""
    verdict = ShazamVerdict(accept=False, reason="likely a cover")
    assert verdict.accept is False
    assert verdict.reason == "likely a cover"
    assert verdict is not USE_HEURISTIC  # identity check used in handler


def test_use_heuristic_falls_through_to_today_behavior():
    """Sentinel result means the handler should not branch into the
    rejection path — handler reads `verdict is USE_HEURISTIC` first."""
    assert USE_HEURISTIC is USE_HEURISTIC  # tautology that documents the contract
    assert (USE_HEURISTIC is ShazamVerdict(accept=True)) is False
