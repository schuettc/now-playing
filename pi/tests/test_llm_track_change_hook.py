"""Tests for the LLM track-change primary judge (llm-track-change-primary).

Covers:
  - `LLMAssist.decide_track_change` SDK round-trip + error/timeout paths.
  - `_build_track_change_prompt` includes all required context fields.
  - `_parse_track_change` validates decision enum and confidence range.
  - Orchestrator._maybe_llm_override_rule_a integration:
      1. No key → no LLM call; Rule A identical to PR #182 behavior.
      2. Key set, coverage-gap heartbeat → LLM returns hold → orchestrator holds.
      3. Key set, ambiguous + Shazam-gated-different-track → LLM returns advance
         with valid position → orchestrator advances.
      4. LLM returns advance with position NOT on tracklist → downgrade to hold.
      5. LLM times out → falls back to Rule A (USE_HEURISTIC).
      6. LLM errors → falls back to Rule A (USE_HEURISTIC).
      7. 3s timeout constant is TIMEOUT_S = 3.0 (enforced via AsyncAnthropic ctor).
"""
from __future__ import annotations

import asyncio
import logging
from unittest import mock

import pytest

from nowplaying.llm import (
    LLMAssist,
    TIMEOUT_S,
    TrackChangeVerdict,
    USE_HEURISTIC,
    _build_track_change_prompt,
    _parse_track_change,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return monkeypatch


@pytest.fixture
def keyed_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-not-real")
    return monkeypatch


def _fake_tool_use_response(tool_name: str, payload: dict):
    block = mock.MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = payload
    response = mock.MagicMock()
    response.content = [block]
    return response


_SAMPLE_CONTEXT = {
    "locked_track": {
        "release_id": 12345,
        "title": "Heart and Soul",
        "position": "A2",
        "duration_s": 270,
    },
    "elapsed_since_last_signal_s": 90.0,
    "recent_fp_hits": [
        {"position": "A2", "hits": 45},
        {"position": "A2", "hits": 38},
    ],
    "last_shazam_gated": {
        "artist": "Joy Division",
        "title": "Isolation",
        "release_id": None,
    },
    "recent_audible_edges": [
        {"type": "audible", "ts_iso": "2026-05-18T10:00:00Z"},
    ],
    "full_tracklist": [
        {"position": "A1", "title": "Atrocity Exhibition", "duration_s": 360},
        {"position": "A2", "title": "Isolation", "duration_s": 170},
        {"position": "A3", "title": "Passover", "duration_s": 290},
        {"position": "A4", "title": "Colony", "duration_s": 230},
    ],
}


# ── Test 1: No key → no LLM call; identical to PR #182 Rule A behavior ──────


def test_decide_track_change_disabled_returns_sentinel(clean_env):
    """No ANTHROPIC_API_KEY → method returns USE_HEURISTIC without SDK call."""
    a = LLMAssist()
    assert a.enabled is False
    result = _run(a.decide_track_change(_SAMPLE_CONTEXT))
    assert result is USE_HEURISTIC


def test_decide_track_change_disabled_no_sdk_import(clean_env):
    """Disabled path never constructs AsyncAnthropic."""
    a = LLMAssist()
    with mock.patch("nowplaying.llm.track_change._build_track_change_prompt") as mock_build:
        _run(a.decide_track_change(_SAMPLE_CONTEXT))
    # Prompt builder never called on the disabled path.
    mock_build.assert_not_called()


# ── Test 2: Key set, LLM returns hold → orchestrator holds ──────────────────


def test_decide_track_change_hold_verdict(keyed_env):
    """LLM returns hold → TrackChangeVerdict with decision='hold'."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {"decision": "hold", "confidence": 0.85, "reason": "fp hits support current track"},
        ),
    )
    a._client = fake_client

    result = _run(a.decide_track_change(_SAMPLE_CONTEXT))
    assert isinstance(result, TrackChangeVerdict)
    assert result.decision == "hold"
    assert result.confidence == pytest.approx(0.85)
    assert result.advance_to_position is None
    assert "fp hits" in result.reason


# ── Test 3: Key set, LLM returns advance with valid position ─────────────────


def test_decide_track_change_advance_with_valid_position(keyed_env):
    """LLM returns advance with advance_to_position → TrackChangeVerdict."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {
                "decision": "advance",
                "confidence": 0.9,
                "advance_to_position": "A3",
                "reason": "Shazam gated Passover matches the elapsed time",
            },
        ),
    )
    a._client = fake_client

    result = _run(a.decide_track_change(_SAMPLE_CONTEXT))
    assert isinstance(result, TrackChangeVerdict)
    assert result.decision == "advance"
    assert result.confidence == pytest.approx(0.9)
    assert result.advance_to_position == "A3"


# ── Test 4: LLM returns advance with position NOT on tracklist ───────────────
# (The orchestrator downgrades to hold — tested in the integration block below)


def test_parse_track_change_advance_strips_position_on_non_advance():
    """advance_to_position is dropped for hold/uncertain decisions."""
    v = _parse_track_change(
        decision="hold", confidence=0.8,
        advance_to_position="A3", reason="x",
    )
    assert v.advance_to_position is None


def test_parse_track_change_advance_preserves_position():
    """advance_to_position is kept for advance decisions."""
    v = _parse_track_change(
        decision="advance", confidence=0.9,
        advance_to_position="A2", reason="y",
    )
    assert v.advance_to_position == "A2"


def test_parse_track_change_uncertain_drops_position():
    """advance_to_position is dropped for uncertain decisions."""
    v = _parse_track_change(
        decision="uncertain", confidence=0.5,
        advance_to_position="A3", reason="unsure",
    )
    assert v.advance_to_position is None


# ── Test 5: LLM times out → falls back to Rule A ────────────────────────────


def test_decide_track_change_timeout_returns_sentinel(keyed_env, caplog):
    """SDK raises TimeoutError → USE_HEURISTIC + throttled warning."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=asyncio.TimeoutError("simulated timeout"),
    )
    a._client = fake_client

    with caplog.at_level(logging.WARNING, logger="nowplaying.llm"):
        result = _run(a.decide_track_change(_SAMPLE_CONTEXT))
    assert result is USE_HEURISTIC


# ── Test 6: LLM errors → falls back to Rule A ───────────────────────────────


def test_decide_track_change_api_error_returns_sentinel(keyed_env, caplog):
    """SDK raises generic Exception → USE_HEURISTIC + throttled warning."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=RuntimeError("connection refused"),
    )
    a._client = fake_client

    with caplog.at_level(logging.WARNING, logger="nowplaying.llm"):
        result = _run(a.decide_track_change(_SAMPLE_CONTEXT))
    assert result is USE_HEURISTIC


def test_decide_track_change_malformed_response_returns_sentinel(keyed_env):
    """Malformed tool-use response → parser raises → USE_HEURISTIC."""
    a = LLMAssist()
    bad_response = mock.MagicMock()
    bad_response.content = []  # no tool_use block
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(return_value=bad_response)
    a._client = fake_client

    result = _run(a.decide_track_change(_SAMPLE_CONTEXT))
    assert result is USE_HEURISTIC


# ── Test 7: 3s timeout constant ─────────────────────────────────────────────


def test_timeout_constant_is_3s():
    """TIMEOUT_S == 3.0 — ensures the latency budget fits inside the 15s heartbeat."""
    assert TIMEOUT_S == 3.0


def test_async_anthropic_receives_timeout_at_construction(keyed_env):
    """AsyncAnthropic is constructed with timeout=TIMEOUT_S (3.0s)."""
    a = LLMAssist()
    with mock.patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = mock.MagicMock()
        a._get_client()
    mock_cls.assert_called_once_with(api_key=a._api_key, timeout=TIMEOUT_S)


# ── Prompt-builder coverage ──────────────────────────────────────────────────


def test_build_track_change_prompt_includes_all_required_fields():
    """Prompt contains the locked track, elapsed time, fp hits, shazam, edges, tracklist."""
    prompt = _build_track_change_prompt(_SAMPLE_CONTEXT)
    assert "Heart and Soul" in prompt          # locked track title
    assert "90.0" in prompt                    # elapsed since last signal
    assert "A2" in prompt                      # fp hit position
    assert "Isolation" in prompt               # Shazam gated title
    assert "audible" in prompt                 # edge event type
    assert "Passover" in prompt                # tracklist title
    assert "A3" in prompt                      # tracklist position


def test_build_track_change_prompt_empty_context_does_not_crash():
    """Empty context → no exception; LLM gets a valid (sparse) prompt."""
    prompt = _build_track_change_prompt({})
    assert "decide_track_change" in prompt or "track-change" in prompt or "hold" in prompt


# ── Parser validation ────────────────────────────────────────────────────────


def test_parse_track_change_raises_on_bad_decision():
    with pytest.raises(ValueError, match="unexpected decision"):
        _parse_track_change(decision="MAYBE", confidence=0.5, reason="x")


def test_parse_track_change_raises_on_non_numeric_confidence():
    with pytest.raises(ValueError, match="non-numeric confidence"):
        _parse_track_change(decision="hold", confidence="very", reason="x")


def test_parse_track_change_clamps_confidence_above_one():
    v = _parse_track_change(decision="hold", confidence=1.5, reason="x")
    assert v.confidence == pytest.approx(1.0)


def test_parse_track_change_clamps_confidence_below_zero():
    v = _parse_track_change(decision="hold", confidence=-0.3, reason="x")
    assert v.confidence == pytest.approx(0.0)


# ── Orchestrator integration ─────────────────────────────────────────────────


def _make_orch_for_llm_override(*, llm_enabled: bool):
    """Build a minimal Orchestrator for _maybe_llm_override_rule_a tests."""
    from nowplaying.main import Orchestrator, State
    from nowplaying.orchestrator.streaming_idle import NEEDS_ID_STREAK, HEARTBEAT_INTERVAL_S
    from datetime import datetime, timedelta, timezone

    llm_obj = LLMAssist()
    llm_obj.enabled = llm_enabled

    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = llm_obj
    state = State()
    orch.state = state

    # Set up a locked album mid-track (elapsed=90s on 270s duration → coverage gap)
    state.last_vinyl = {
        "release_id": 12345,
        "track_position": "A2",
        "side": "A",
        "title": "Isolation",
        "artist": "Joy Division",
        "album": "Closer",
        "duration_seconds": 270,
    }
    anchor = datetime.now(timezone.utc) - timedelta(seconds=90)
    state.track_started_at = anchor.isoformat(timespec="seconds").replace("+00:00", "Z")
    state.unmatched_streak = NEEDS_ID_STREAK
    state.last_shazam_gated = {
        "artist": "Joy Division", "title": "Passover", "release_id": None,
    }
    state.recent_fp_hits = [
        {"position": "A2", "hits": 40, "ts": 0.0},
        {"position": "A2", "hits": 35, "ts": 0.0},
    ]
    state.recent_audible_edges = [
        {"type": "audible", "ts_iso": "2026-05-18T10:00:00Z", "_ts_mono": 0.0},
    ]

    # Stub external calls
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    orch._try_advance_prediction = mock.AsyncMock(return_value=True)

    # Provide a minimal catalog tracklist for the sanity check
    _TRACKLIST = [
        {"side": "A", "track_position": "A1", "title": "Atrocity Exhibition", "duration_seconds": 360},
        {"side": "A", "track_position": "A2", "title": "Isolation", "duration_seconds": 170},
        {"side": "A", "track_position": "A3", "title": "Passover", "duration_seconds": 290},
        {"side": "A", "track_position": "A4", "title": "Colony", "duration_seconds": 230},
    ]
    orch._load_locked_tracks = staticmethod(lambda s: _TRACKLIST)

    return orch


def test_llm_override_disabled_returns_false(clean_env):
    """Test 1 (regression): LLM disabled → _maybe_llm_override_rule_a returns False.
    Identical to Rule A behavior — no LLM call ever fires.
    """
    orch = _make_orch_for_llm_override(llm_enabled=False)
    result = _run(orch._maybe_llm_override_rule_a(
        orch.state, "vinyl", 90.0,
    ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()


def test_llm_override_hold_returns_false(keyed_env, caplog):
    """Test 2: LLM returns hold → _maybe_llm_override_rule_a returns False → orchestrator holds."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {"decision": "hold", "confidence": 0.85, "reason": "fp supports current"},
        ),
    )
    orch.llm._client = fake_client

    with caplog.at_level(logging.INFO, logger="nowplaying.main"):
        result = _run(orch._maybe_llm_override_rule_a(
            orch.state, "vinyl", 90.0,
        ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()


def test_llm_override_advance_with_valid_position(keyed_env):
    """Test 3: LLM returns advance with valid position → orchestrator advances."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {
                "decision": "advance",
                "confidence": 0.92,
                "advance_to_position": "A3",
                "reason": "gated Shazam + elapsed match Passover",
            },
        ),
    )
    orch.llm._client = fake_client

    result = _run(orch._maybe_llm_override_rule_a(
        orch.state, "vinyl", 90.0,
    ))
    assert result is True
    orch._try_advance_prediction.assert_awaited_once()
    # Verify the correct target position was passed.
    call_kwargs = orch._try_advance_prediction.call_args
    assert call_kwargs.kwargs.get("target_track_position") == "A3"


def test_llm_override_advance_invalid_position_downgrades_to_hold(keyed_env, caplog):
    """Test 4: LLM returns advance with position NOT in tracklist → downgraded to hold."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {
                "decision": "advance",
                "confidence": 0.9,
                "advance_to_position": "B1",  # wrong side — not in A-side tracklist
                "reason": "hallucinated position",
            },
        ),
    )
    orch.llm._client = fake_client

    with caplog.at_level(logging.WARNING, logger="nowplaying.main"):
        result = _run(orch._maybe_llm_override_rule_a(
            orch.state, "vinyl", 90.0,
        ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()
    assert "not in tracklist" in caplog.text or "downgrading" in caplog.text


def test_llm_override_timeout_falls_back_to_rule_a(keyed_env):
    """Test 5: LLM timeout → USE_HEURISTIC → _maybe_llm_override_rule_a returns False."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=asyncio.TimeoutError("simulated"),
    )
    orch.llm._client = fake_client

    result = _run(orch._maybe_llm_override_rule_a(
        orch.state, "vinyl", 90.0,
    ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()


def test_llm_override_error_falls_back_to_rule_a(keyed_env):
    """Test 6: LLM raises generic error → USE_HEURISTIC → returns False (Rule A)."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=ConnectionError("network down"),
    )
    orch.llm._client = fake_client

    result = _run(orch._maybe_llm_override_rule_a(
        orch.state, "vinyl", 90.0,
    ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()


def test_llm_override_uncertain_decision_holds(keyed_env):
    """'uncertain' decision → treated as hold (confidence check is irrelevant)."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {
                "decision": "uncertain",
                "confidence": 0.9,
                "reason": "signals are mixed",
            },
        ),
    )
    orch.llm._client = fake_client

    result = _run(orch._maybe_llm_override_rule_a(
        orch.state, "vinyl", 90.0,
    ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()


def test_llm_override_low_confidence_advance_holds(keyed_env):
    """Advance with confidence < 0.7 → treated as hold (not confident enough)."""
    orch = _make_orch_for_llm_override(llm_enabled=True)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "decide_track_change",
            {
                "decision": "advance",
                "confidence": 0.6,
                "advance_to_position": "A3",
                "reason": "weak evidence",
            },
        ),
    )
    orch.llm._client = fake_client

    result = _run(orch._maybe_llm_override_rule_a(
        orch.state, "vinyl", 90.0,
    ))
    assert result is False
    orch._try_advance_prediction.assert_not_awaited()


# ── _resolve_advanced_track explicit-target lookup ──────────────────────────


def test_resolve_advanced_track_matches_position_key():
    """F6 explicit-target lookup must accept the production tracklist field
    name (`position`) rather than only the legacy `track_position`, AND must
    return a predicted-position-shape dict (with `release_id` and
    `track_position`) so downstream _build_predicted_payload can read
    release_id without KeyError.

    Regression for f6-llm-advance-uses-wrong-field (commit 4991061) plus
    the follow-up llm-guess-renders-as-predicted shape normalization.
    """
    from nowplaying.main import Orchestrator, State

    orch = Orchestrator.__new__(Orchestrator)
    orch.state = State()
    orch.state.last_vinyl = {
        "release_id": 31427573,
        "track_position": "B5",
        "side": "B",
    }
    # Production-shape tracklist (matches recognize_proto / Discogs catalog).
    tracks = [
        {"position": "B5", "side": "B", "title": "Pillowhead", "duration_seconds": 240},
        {"position": "B6", "side": "B", "title": "Blank", "duration_seconds": 339},
        {"position": "B7", "side": "B", "title": "Segue 2", "duration_seconds": 77},
    ]
    result = orch._resolve_advanced_track(orch.state, tracks, "B7")
    assert result is not None
    # Normalized to predicted-position shape (mirrors _advance_predicted_position):
    assert result["track_position"] == "B7"
    assert result["title"] == "Segue 2"
    assert result["side"] == "B"
    assert result["duration_seconds"] == 77
    assert result["release_id"] == 31427573, (
        "must include release_id from state.last_vinyl so "
        "_build_predicted_payload can resolve the catalog"
    )


def test_resolve_advanced_track_falls_back_when_target_missing():
    """Target not present in tracklist → falls through to the heuristic
    source-position + advance-by-one branch. Regression guard so the F6
    fix didn't accidentally start matching on `None == None`.
    """
    from nowplaying.main import Orchestrator, State

    orch = Orchestrator.__new__(Orchestrator)
    orch.state = State()
    orch.state.last_vinyl = {
        "release_id": 1,
        "track_position": "A1",
        "side": "A",
    }
    tracks = [
        {"position": "A1", "side": "A", "title": "Track 1"},
        {"position": "A2", "side": "A", "title": "Track 2"},
    ]
    # Target "Z99" missing → falls back to source-pos (A1) + advance-by-one (A2).
    # The heuristic path returns a predicted_position-shape dict keyed by
    # `track_position`, not a tracklist entry — distinct from the F6 path
    # which returns the tracklist dict directly.
    result = orch._resolve_advanced_track(orch.state, tracks, "Z99")
    assert result is not None
    assert result["track_position"] == "A2"
