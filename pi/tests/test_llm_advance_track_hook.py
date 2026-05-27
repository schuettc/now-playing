"""F6: Tests for the LLM-advance-track hook on the Orchestrator and the
real `judge_advance` implementation."""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying import llm as llm_mod
from nowplaying.llm import AdvanceVerdict, LLMAssist, USE_HEURISTIC


def _run(coro):
    return asyncio.run(coro)


def _fake_tool_use_response(tool_name: str, payload: dict):
    block = mock.MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = payload
    response = mock.MagicMock()
    response.content = [block]
    return response


# ── judge_advance unit tests ─────────────────────────────────────────────


def test_judge_advance_returns_index_verdict(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_advance", {"advance_to_index": 2, "reason": "elapsed exceeds duration"},
        ),
    )
    a._client = fake_client

    side = [{"track_position": "A1", "title": "T1"}, {"track_position": "A2", "title": "T2"}, {"track_position": "A3", "title": "T3"}]
    verdict = _run(a.judge_advance(elapsed_s=240.0, last_track={"title": "T1"}, side_tracklist=side))
    assert isinstance(verdict, AdvanceVerdict)
    assert verdict.advance_to_index == 2
    assert "elapsed" in verdict.reason


def test_judge_advance_returns_stay_verdict(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_advance", {"advance_to_index": None, "reason": "still on long track"},
        ),
    )
    a._client = fake_client

    verdict = _run(a.judge_advance(elapsed_s=30.0, last_track={"title": "Long"}, side_tracklist=[{"track_position": "A1"}, {"track_position": "A2"}]))
    assert verdict.advance_to_index is None
    assert verdict.reason


def test_judge_advance_falls_back_on_malformed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    a = LLMAssist()
    bad = mock.MagicMock()
    bad.content = []
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(return_value=bad)
    a._client = fake_client
    result = _run(a.judge_advance(elapsed_s=60.0, last_track=None, side_tracklist=[{"x": 1}, {"y": 2}]))
    assert result is USE_HEURISTIC


def test_judge_advance_disabled_returns_sentinel(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = LLMAssist()
    assert _run(a.judge_advance(60.0, None, [])) is USE_HEURISTIC


def test_build_advance_prompt_includes_elapsed_and_tracklist():
    prompt = llm_mod._build_advance_prompt(
        elapsed_s=240.5,
        last_track={"title": "Heart and Soul", "track_position": "A1", "side": "A"},
        side_tracklist=[{"track_position": "A1", "title": "Heart and Soul"}, {"track_position": "A2", "title": "Twenty Four Hours"}],
    )
    assert "240" in prompt or "240.5" in prompt
    assert "Heart and Soul" in prompt
    assert "Twenty Four Hours" in prompt


# ── Orchestrator hook integration ────────────────────────────────────────


@pytest.fixture
def orch_with_lock():
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = LLMAssist()
    orch.llm.enabled = True
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = {
        "title": "Heart and Soul",
        "track_position": "D1",
        "side": "D",
    }
    orch.state.track_started_at = None  # forces seed_back_s fallback
    return orch


def test_maybe_consult_advance_skips_when_disabled():
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = {"side": "A", "title": "x"}
    result = _run(orch._maybe_consult_llm_for_advance(orch.state, 60.0))
    assert result is None


def test_maybe_consult_advance_skips_when_no_lock(orch_with_lock):
    orch_with_lock.state.last_vinyl = None
    result = _run(orch_with_lock._maybe_consult_llm_for_advance(orch_with_lock.state, 60.0))
    assert result is None


def test_maybe_consult_advance_skips_when_side_has_one_track(orch_with_lock, monkeypatch):
    monkeypatch.setattr(
        type(orch_with_lock), "_load_locked_tracks",
        staticmethod(lambda state: [{"side": "D", "track_position": "D1"}]),
    )
    result = _run(orch_with_lock._maybe_consult_llm_for_advance(orch_with_lock.state, 60.0))
    assert result is None


def test_maybe_consult_advance_returns_target_on_index_verdict(orch_with_lock, monkeypatch):
    monkeypatch.setattr(
        type(orch_with_lock), "_load_locked_tracks",
        staticmethod(lambda state: [
            {"side": "D", "track_position": "D1"},
            {"side": "D", "track_position": "D2"},
            {"side": "D", "track_position": "D3"},
        ]),
    )
    orch_with_lock.llm.judge_advance = mock.AsyncMock(
        return_value=AdvanceVerdict(advance_to_index=2, reason="picked D3"),
    )
    result = _run(orch_with_lock._maybe_consult_llm_for_advance(orch_with_lock.state, 60.0))
    assert result == "D3"


def test_maybe_consult_advance_returns_STAY_on_null_verdict(orch_with_lock, monkeypatch):
    monkeypatch.setattr(
        type(orch_with_lock), "_load_locked_tracks",
        staticmethod(lambda state: [
            {"side": "D", "track_position": "D1"},
            {"side": "D", "track_position": "D2"},
        ]),
    )
    orch_with_lock.llm.judge_advance = mock.AsyncMock(
        return_value=AdvanceVerdict(advance_to_index=None, reason="long track"),
    )
    result = _run(orch_with_lock._maybe_consult_llm_for_advance(orch_with_lock.state, 60.0))
    assert result == "STAY"


def test_maybe_consult_advance_clamps_out_of_range(orch_with_lock, monkeypatch, caplog):
    monkeypatch.setattr(
        type(orch_with_lock), "_load_locked_tracks",
        staticmethod(lambda state: [
            {"side": "D", "track_position": "D1"},
            {"side": "D", "track_position": "D2"},
        ]),
    )
    orch_with_lock.llm.judge_advance = mock.AsyncMock(
        return_value=AdvanceVerdict(advance_to_index=99, reason="bad"),
    )
    import logging
    with caplog.at_level(logging.WARNING):
        result = _run(orch_with_lock._maybe_consult_llm_for_advance(orch_with_lock.state, 60.0))
    assert result is None
    assert any("out-of-range" in r.getMessage() for r in caplog.records)


def test_maybe_consult_advance_falls_through_on_sentinel(orch_with_lock, monkeypatch):
    monkeypatch.setattr(
        type(orch_with_lock), "_load_locked_tracks",
        staticmethod(lambda state: [
            {"side": "D", "track_position": "D1"},
            {"side": "D", "track_position": "D2"},
        ]),
    )
    orch_with_lock.llm.judge_advance = mock.AsyncMock(return_value=USE_HEURISTIC)
    result = _run(orch_with_lock._maybe_consult_llm_for_advance(orch_with_lock.state, 60.0))
    assert result is None


# ── Anti-hedge regression ──────────────────────────────────────────────────


def test_build_advance_prompt_explicitly_disallows_hedging():
    """Regression for llm-judge-advance-track-guess-divergence.

    Caught live on Donuts 2026-05-21 B5→B6 transition: judge_advance saw
    elapsed=149s, last_track.duration_s=115s (Lightworks), and hedged
    into "stay" reasoning "elapsed is still within Lightworks' typical
    duration of 115 seconds, but close" — internally inconsistent
    arithmetic. judge_track_guess on the same heartbeat correctly
    advanced to B6 Stepson Of The Clapper. The prompt now explicitly
    forbids hedging when durations are populated and elapsed > duration.
    """
    import nowplaying.llm.advance as llm_mod

    prompt = llm_mod._build_advance_prompt(
        elapsed_s=149.0,
        last_track={
            "title": "Lightworks",
            "track_position": "B5",
            "side": "B",
        },
        side_tracklist=[
            {"track_position": "B5", "title": "Lightworks", "duration_seconds": 115},
            {"track_position": "B6", "title": "Stepson Of The Clapper", "duration_seconds": 61},
        ],
    )
    # The fix removes the soft "prefer to stay" weighting and adds an
    # arithmetic-first rule. Verify the new language is present.
    assert "arithmetically" in prompt.lower() or "arithmetic" in prompt.lower()
    assert "do not hedge" in prompt.lower() or "do NOT hedge" in prompt
    # The numeric inputs are still present for the model.
    assert "149" in prompt
    assert "B5" in prompt
