"""Tests for the LLM track-guess hook (Feature C of
`confirmed-fingerprint-coverage`).

Covers:
  - `LLMAssist.judge_track_guess` SDK round-trip + cache + error paths.
  - `_build_track_guess_prompt` cache-stability invariants
    (current-track filtering + 5s bucketing).
  - `_parse_track_guess` alt-only-on-medium normalization.
  - `Orchestrator._compute_track_guess` LLM + heuristic + None paths.
  - `state.pending_guess` attach/clear lifecycle through
    `_anchor_and_publish`.
"""
from __future__ import annotations

import asyncio
import logging
from unittest import mock

import pytest

from nowplaying.llm import (
    LLMAssist,
    TrackGuess,
    USE_HEURISTIC,
    _build_track_guess_prompt,
    _parse_track_guess,
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


_SAMPLE_CALL_KWARGS = dict(
    locked_album_ctx={
        "locked_artist": "Joy Division",
        "locked_album": "Closer",
        "locked_release_id": 12345,
        "locked_side": "A",
        "locked_title": "Heart and Soul",
    },
    side_tracklist=[
        {"track_position": "A1", "title": "Atrocity Exhibition", "duration_seconds": 360},
        {"track_position": "A2", "title": "Isolation", "duration_seconds": 170},
        {"track_position": "A3", "title": "Passover", "duration_seconds": 290},
        {"track_position": "A4", "title": "Colony", "duration_seconds": 230},
    ],
    recent_history=[
        {"artist": "Joy Division", "title": "Isolation"},
        {"artist": "Joy Division", "title": "Atrocity Exhibition"},
    ],
    audible_up_iso="2026-05-16T10:00:00Z",
    elapsed_since_audible_up_s=120.0,
    elapsed_since_last_confirm_s=120.0,
    predicted_position="A3",
    likely_flip=False,
    next_side_first=None,
)


# ── Disabled-without-key path ───────────────────────────────────────────


def test_judge_track_guess_disabled_returns_sentinel(clean_env):
    """No key → method returns USE_HEURISTIC without touching the SDK."""
    a = LLMAssist()
    assert a.enabled is False
    result = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert result is USE_HEURISTIC


# ── Successful SDK round-trip + cache ───────────────────────────────────


def test_judge_track_guess_returns_track_guess(keyed_env):
    """Successful Anthropic call → parsed TrackGuess; high-confidence has alt=None."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_track_guess",
            {"position": "A3", "confidence": "high", "reason": "elapsed matches"},
        ),
    )
    a._client = fake_client

    r = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert isinstance(r, TrackGuess)
    assert r.position == "A3"
    assert r.confidence == "high"
    assert r.source == "llm"
    assert r.alt is None
    assert r.reason == "elapsed matches"


def test_judge_track_guess_medium_preserves_alt(keyed_env):
    """Medium-confidence with alt → both surfaced."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_track_guess",
            {
                "position": "A2", "confidence": "medium",
                "alt": {"position": "A3"}, "reason": "two candidates",
            },
        ),
    )
    a._client = fake_client

    r = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert r.confidence == "medium"
    assert r.position == "A2"
    assert r.alt == {"position": "A3"}


def test_judge_track_guess_drops_alt_for_high_confidence(keyed_env):
    """High-confidence with alt set by LLM → alt is dropped post-parse."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_track_guess",
            {
                "position": "A3", "confidence": "high",
                "alt": {"position": "A4"}, "reason": "ignore me",
            },
        ),
    )
    a._client = fake_client

    r = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert r.confidence == "high"
    assert r.alt is None  # defense in depth — prompt forbade it, parser enforces


def test_judge_track_guess_drops_alt_for_low_confidence(keyed_env):
    """Low-confidence with alt set by LLM → alt is dropped post-parse."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_track_guess",
            {
                "position": "A2", "confidence": "low",
                "alt": {"position": "A3"}, "reason": "shot in the dark",
            },
        ),
    )
    a._client = fake_client

    r = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert r.confidence == "low"
    assert r.alt is None


def test_judge_track_guess_cache_hit_on_repeat(keyed_env):
    """Same call args → SDK invoked exactly once across two calls."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_track_guess",
            {"position": "A3", "confidence": "high", "reason": "x"},
        ),
    )
    a._client = fake_client

    r1 = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    r2 = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert r2 is r1
    assert fake_client.messages.create.await_count == 1


def test_judge_track_guess_api_timeout_returns_sentinel(keyed_env, caplog):
    """SDK raises TimeoutError → USE_HEURISTIC + throttled warning."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=asyncio.TimeoutError("simulated"),
    )
    a._client = fake_client

    with caplog.at_level(logging.WARNING, logger="nowplaying.llm"):
        r = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert r is USE_HEURISTIC


def test_judge_track_guess_schema_parse_error_returns_sentinel(keyed_env):
    """Bad confidence in tool input → parser raises → USE_HEURISTIC."""
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_track_guess",
            {"position": "A3", "confidence": "WAT", "reason": "bad"},
        ),
    )
    a._client = fake_client

    r = _run(a.judge_track_guess(**_SAMPLE_CALL_KWARGS))
    assert r is USE_HEURISTIC


# ── Prompt-builder cache-stability invariants ──────────────────────────


def test_build_track_guess_prompt_filters_current_track_from_history():
    """The locked album's (artist,title) is dropped from recent_history
    before serialization — same effective context every heartbeat of the
    same track."""
    kw = dict(_SAMPLE_CALL_KWARGS)
    # History where the current track (Heart and Soul) IS present.
    kw_with_current = {
        **kw,
        "recent_history": [
            {"artist": "Joy Division", "title": "Heart and Soul"},
            {"artist": "Joy Division", "title": "Isolation"},
        ],
    }
    # And one WITHOUT it.
    kw_without_current = {
        **kw,
        "recent_history": [
            {"artist": "Joy Division", "title": "Isolation"},
        ],
    }
    p1 = _build_track_guess_prompt(**kw_with_current)
    p2 = _build_track_guess_prompt(**kw_without_current)
    assert p1 == p2, "current track must be filtered out for cache stability"


def test_build_track_guess_prompt_buckets_elapsed_since_audible_up():
    """elapsed_since_audible_up_s rounds to 5s buckets so heartbeats
    hash identically.

    Bucket math: `int(round(s / 5.0) * 5)`. Values 48.0..52.4 all map to
    bucket 50. Verify multiple values in the same bucket produce
    identical prompts.
    """
    p_48 = _build_track_guess_prompt(**{**_SAMPLE_CALL_KWARGS, "elapsed_since_audible_up_s": 48.0})
    p_49 = _build_track_guess_prompt(**{**_SAMPLE_CALL_KWARGS, "elapsed_since_audible_up_s": 49.1})
    p_50 = _build_track_guess_prompt(**{**_SAMPLE_CALL_KWARGS, "elapsed_since_audible_up_s": 50.0})
    p_52 = _build_track_guess_prompt(**{**_SAMPLE_CALL_KWARGS, "elapsed_since_audible_up_s": 52.0})
    assert p_48 == p_49 == p_50 == p_52, "48..52s should hash to the 50s bucket"
    # Different bucket should produce a different prompt.
    p_55 = _build_track_guess_prompt(**{**_SAMPLE_CALL_KWARGS, "elapsed_since_audible_up_s": 55.0})
    assert p_55 != p_50


def test_build_track_guess_prompt_carries_both_elapsed_clocks():
    """Both elapsed fields must appear in the prompt with distinct labels —
    regression guard for the elapsed-frame-confusion fix. The LLM needs to
    distinguish "since needle drop" from "since last confirmed track".
    """
    p = _build_track_guess_prompt(
        **{
            **_SAMPLE_CALL_KWARGS,
            "elapsed_since_audible_up_s": 240.0,
            "elapsed_since_last_confirm_s": 30.0,
        },
    )
    assert "audible-up" in p.lower() or "needle-drop" in p.lower()
    assert "since last confirmed" in p.lower()
    assert "240" in p
    assert "30" in p


def test_build_track_guess_prompt_includes_tracklist_and_predicted_position():
    """Defensive: prompt contains the inputs the LLM needs to ground its pick."""
    p = _build_track_guess_prompt(**_SAMPLE_CALL_KWARGS)
    assert "Closer" in p
    assert "A3" in p  # predicted position
    assert "Passover" in p  # title for A3


def test_build_track_guess_prompt_includes_estimated_position_and_windows():
    """Cumulative windows (start_s/end_s) and the precomputed estimated
    position are rendered so the model does a window lookup, not arithmetic."""
    p = _build_track_guess_prompt(
        **{**_SAMPLE_CALL_KWARGS, "estimated_side_position_s": 163.0},
    )
    # The data line (distinct from the header's instruction mention) carries
    # the bucketed value; 163 buckets to 165.
    assert "anchored to the confirmed track): 165" in p
    assert "start_s" in p and "end_s" in p
    # Data line omitted when no estimate is provided (header guidance remains).
    assert "anchored to the confirmed track):" not in _build_track_guess_prompt(
        **_SAMPLE_CALL_KWARGS,
    )


def test_cum_start_s_sums_preceding_durations():
    from nowplaying.orchestrator.llm._track_guess import _cum_start_s
    side = [
        {"position": "A1", "duration_seconds": 190},
        {"position": "A2", "duration_seconds": 177},
        {"position": "A3", "duration_seconds": 48},
    ]
    assert _cum_start_s(side, "A1") == 0
    assert _cum_start_s(side, "A2") == 190
    assert _cum_start_s(side, "A3") == 367
    assert _cum_start_s(side, "Z9") is None


def test_position_for_side_offset_interval_lookup():
    """Deterministic interval lookup — the always-correct replacement for the
    LLM's botched comparison. The est=100 case is exactly the one the LLM got
    wrong (it placed 100 in A2's [190,367] window)."""
    from nowplaying.orchestrator.llm._track_guess import _position_for_side_offset
    side = [
        {"position": "A1", "duration_seconds": 190},
        {"position": "A2", "duration_seconds": 177},
        {"position": "A3", "duration_seconds": 48},
    ]
    assert _position_for_side_offset(side, 0) == "A1"
    assert _position_for_side_offset(side, 100) == "A1"   # LLM wrongly said A2
    assert _position_for_side_offset(side, 189) == "A1"
    assert _position_for_side_offset(side, 190) == "A2"   # boundary → next track
    assert _position_for_side_offset(side, 300) == "A2"
    assert _position_for_side_offset(side, 367) == "A3"
    assert _position_for_side_offset(side, 999) == "A3"   # overflow → last track
    assert _position_for_side_offset([], 50) is None


def test_guard_no_backward_side_pos():
    """A guess earlier on the side than the current track is clamped to the
    current track — a record can't play backward (the live B4→B2 case).
    Forward guesses, same-track, and off-side positions pass through."""
    from nowplaying.orchestrator.llm._track_guess import TrackGuessMixin
    side = [
        {"position": "B1"}, {"position": "B2"}, {"position": "B3"},
        {"position": "B4"}, {"position": "B5"},
    ]
    g = TrackGuessMixin._guard_no_backward_side_pos
    assert g(side, "B4", "B2") == "B4"   # backward → hold current
    assert g(side, "B4", "B5") == "B5"   # forward → allow
    assert g(side, "B4", "B4") == "B4"   # same → allow
    assert g(side, None, "B2") == "B2"   # no current track → no guard
    assert g(side, "B4", "Z9") == "Z9"   # guessed pos not on side → no guard
    assert g(side, "C1", "B2") == "B2"   # current off-side (flip) → no guard


def test_try_window_track_guess_holds_on_backward_estimate(monkeypatch):
    """Wiring: when the side-position estimate collapses and the window lands
    on a track earlier than the current one, the guess holds the current track
    instead of jumping backward (regression for the live B4 'She Hates My Job'
    → B2 'Lead Pipe Cinch' jump)."""
    orch = _make_orch(llm_enabled=False)
    side = [
        {"position": "B1", "title": "Fine And Good", "duration_seconds": 249},
        {"position": "B2", "title": "Lead Pipe Cinch", "duration_seconds": 65},
        {"position": "B3", "title": "Cool Magnet", "duration_seconds": 248},
        {"position": "B4", "title": "She Hates My Job", "duration_seconds": 249},
        {"position": "B5", "title": "Stoney", "duration_seconds": 102},
    ]
    orch.state.last_vinyl = {"track_position": "B4", "tracklist": side}
    # Collapsed estimate (267s) lands in B2's window — the backward jump.
    monkeypatch.setattr(
        orch, "_estimate_side_position_s",
        lambda *a, **k: 267.0, raising=True,
    )
    # Not testing dismissal here (it needs a running loop) — short-circuit it.
    monkeypatch.setattr(
        orch, "_guess_is_dismissed_for", lambda *a, **k: False, raising=True,
    )
    title_for = orch._make_title_for(side)
    guess = orch._try_window_track_guess(orch.state, 12520688, side, title_for, 267.0)
    assert guess is not None
    assert guess["position"] == "B4"          # held, NOT B2
    assert guess["title"] == "She Hates My Job"


def test_estimate_side_position_pin_anchored(monkeypatch):
    """A live pin anchors the estimate to the confirmed track's cumulative
    start + how far into it + pin age — stable across predicted-advance drift."""
    from nowplaying.orchestrator.llm._track_guess import TrackGuessMixin
    side = [
        {"position": "A1", "duration_seconds": 360},
        {"position": "A2", "duration_seconds": 170},
    ]
    state = mock.MagicMock()
    state.user_track_pin = {
        "track_position": "A2",
        "initial_track_position_s": 12.0,
        "monotonic_ts": 1000.0,
    }
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1008.0  # pin age = 8s
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fake_loop)
    # cum_start(A2)=360 + initial 12 + age 8 = 380
    assert TrackGuessMixin._estimate_side_position_s(state, side, 999.0) == 380.0


def test_estimate_side_position_falls_back_to_audible_up_without_pin():
    """No pin → assume the needle dropped at the side's first track and use
    elapsed-since-needle-drop."""
    from nowplaying.orchestrator.llm._track_guess import TrackGuessMixin
    side = [{"position": "A1", "duration_seconds": 360}]
    state = mock.MagicMock()
    state.user_track_pin = None
    assert TrackGuessMixin._estimate_side_position_s(state, side, 120.0) == 120.0


# ── Parser direct ──────────────────────────────────────────────────────


def test_parse_track_guess_raises_on_bad_confidence():
    with pytest.raises(ValueError):
        _parse_track_guess(position="A3", confidence="WHAT", reason="x")


def test_parse_track_guess_drops_malformed_alt():
    """alt with no position → dropped silently."""
    g = _parse_track_guess(
        position="A3", confidence="medium",
        alt={"not_position": "garbage"}, reason="x",
    )
    assert g.alt is None


# ── Orchestrator _compute_track_guess + pending_guess plumbing ─────────


def _make_orch(*, llm_enabled: bool):
    """Build a minimal Orchestrator (matches the pattern from
    `test_llm_shazam_relevance_hook.py`)."""
    from nowplaying.main import Orchestrator, State
    llm_obj = LLMAssist()
    llm_obj.enabled = llm_enabled
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = llm_obj
    orch.state = State()
    return orch


_TRACKLIST = [
    {"track_position": "A1", "side": "A", "title": "Atrocity Exhibition", "duration_seconds": 360},
    {"track_position": "A2", "side": "A", "title": "Isolation", "duration_seconds": 170},
    {"track_position": "A3", "side": "A", "title": "Passover", "duration_seconds": 290},
    {"track_position": "A4", "side": "A", "title": "Colony", "duration_seconds": 230},
]


def test_compute_track_guess_returns_none_when_no_lock():
    orch = _make_orch(llm_enabled=False)
    orch.state.last_vinyl = None
    assert _run(orch._compute_track_guess(orch.state)) is None


def test_compute_track_guess_returns_none_when_llm_disabled_and_no_prediction():
    orch = _make_orch(llm_enabled=False)
    orch.state.last_vinyl = {
        "artist": "Joy Division", "album": "Closer",
        "side": "A", "title": "Heart and Soul",
        "tracklist": _TRACKLIST,
    }
    orch.state.predicted_position = None
    assert _run(orch._compute_track_guess(orch.state)) is None


def test_compute_track_guess_heuristic_fallback_when_llm_disabled():
    orch = _make_orch(llm_enabled=False)
    orch.state.last_vinyl = {
        "artist": "Joy Division", "album": "Closer",
        "side": "A", "title": "Heart and Soul",
        "tracklist": _TRACKLIST,
    }
    orch.state.predicted_position = {
        "release_id": 12345, "side": "A",
        "track_position": "A3", "index_in_side": 2,
    }
    g = _run(orch._compute_track_guess(orch.state))
    assert g == {
        "position": "A3",
        "title": "Passover",
        "confidence": "low",
        "source": "heuristic",
    }


# Production tracklists (built by recognize_proto, _tracklist_from_release,
# and _publish_enrichment) use "position" as the key — not "track_position".
# Regression for best-guess-empty-title-bug: title_for must resolve titles
# from items keyed by "position", or the heuristic guess publishes with
# title="" and the kiosk renders a dead-end BEST GUESS badge.
_TRACKLIST_PRODUCTION_SHAPE = [
    {"position": "A1", "side": "A", "title": "Atrocity Exhibition", "duration_seconds": 360},
    {"position": "A2", "side": "A", "title": "Isolation", "duration_seconds": 170},
    {"position": "A3", "side": "A", "title": "Passover", "duration_seconds": 290},
    {"position": "A4", "side": "A", "title": "Colony", "duration_seconds": 230},
]


def test_compute_track_guess_resolves_title_from_position_keyed_tracklist():
    """Heuristic guess must populate title when tracklist items use the
    production "position" key (not the test-only "track_position" key)."""
    orch = _make_orch(llm_enabled=False)
    orch.state.last_vinyl = {
        "artist": "Joy Division", "album": "Closer",
        "side": "A", "title": "Heart and Soul",
        "tracklist": _TRACKLIST_PRODUCTION_SHAPE,
    }
    orch.state.predicted_position = {
        "release_id": 12345, "side": "A",
        "track_position": "A3", "index_in_side": 2,
    }
    g = _run(orch._compute_track_guess(orch.state))
    assert g is not None
    assert g["position"] == "A3"
    assert g["title"] == "Passover", (
        f"empty title regression — got {g!r}; "
        "title_for likely looking up wrong field name"
    )


def test_pending_guess_attached_at_publish_time():
    """Stashed guess attaches to the next published payload via
    `_anchor_and_publish` and is cleared after consumption."""
    orch = _make_orch(llm_enabled=False)
    orch.state.pending_guess = {
        "position": "A3",
        "title": "Passover",
        "confidence": "low",
        "source": "heuristic",
    }
    payload = {
        "ts": "2026-05-16T10:00:00Z",
        "state": "NEEDS_ID",
        "source": "vinyl",
        "title": None,
        "match_method": "unmatched",
    }
    result = orch._anchor_and_publish(payload)
    # The guess is enriched with the backend contract (epic
    # consolidate-guess-confidence-lifetime / C2): NEEDS_ID has no duration
    # → expires_in_s None; an unmatched now-playing → confirmable True.
    assert result["guess"] == {
        "position": "A3", "title": "Passover",
        "confidence": "low", "source": "heuristic",
        "expires_in_s": None, "confirmable": True,
    }
    assert orch.state.pending_guess is None, "must clear after consumption"


def test_pending_guess_not_attached_when_none():
    """Publish path is a no-op when no guess is stashed."""
    orch = _make_orch(llm_enabled=False)
    orch.state.pending_guess = None
    payload = {
        "ts": "2026-05-16T10:00:00Z",
        "state": "NEEDS_ID",
        "source": "vinyl",
        "title": None,
        "match_method": "unmatched",
    }
    result = orch._anchor_and_publish(payload)
    assert "guess" not in result


# ── Elapsed-frame-confusion regression ─────────────────────────────────────


def test_compute_elapsed_since_audible_up_uses_monotonic_clock(monkeypatch):
    """_compute_elapsed_since_audible_up_s reads the monotonic timestamp
    directly. This clock SURVIVES predicted-advance refreshes of
    track_started_at — that's the whole point of the fix.
    """
    from nowplaying.orchestrator._llm_hooks import LLMHooksMixin
    import asyncio

    # Pin a fake loop time so the test is deterministic.
    class _FakeLoop:
        def time(self) -> float:
            return 1000.0
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _FakeLoop())

    # audible_up was 240s ago in monotonic seconds.
    elapsed = LLMHooksMixin._compute_elapsed_since_audible_up_s(760.0)
    assert elapsed == 240.0


def test_compute_elapsed_since_audible_up_returns_zero_when_unset():
    """Defensive: when audible_up_at_mono is None (idle or pre-first-edge),
    the helper returns 0.0 instead of crashing."""
    from nowplaying.orchestrator._llm_hooks import LLMHooksMixin

    assert LLMHooksMixin._compute_elapsed_since_audible_up_s(None) == 0.0


# ── Side-flip detection (Option 1) ─────────────────────────────────────


# Two-side catalog for flip tests. Side A has 4 tracks summing to ~1050s;
# A4 is the deep-into-side last-confirmed candidate. Side B opens with B1.
_TWO_SIDE_TRACKLIST = [
    {"track_position": "A1", "side": "A", "title": "Atrocity Exhibition", "duration_seconds": 360},
    {"track_position": "A2", "side": "A", "title": "Isolation", "duration_seconds": 170},
    {"track_position": "A3", "side": "A", "title": "Passover", "duration_seconds": 290},
    {"track_position": "A4", "side": "A", "title": "Colony", "duration_seconds": 230},
    {"track_position": "B1", "side": "B", "title": "A Means to an End", "duration_seconds": 250},
    {"track_position": "B2", "side": "B", "title": "Heart and Soul", "duration_seconds": 350},
    {"track_position": "B3", "side": "B", "title": "Twenty Four Hours", "duration_seconds": 270},
    {"track_position": "B4", "side": "B", "title": "The Eternal", "duration_seconds": 400},
]


def test_build_track_guess_prompt_renders_flip_guidance_when_true():
    """`likely_flip=True` → prompt carries the side-progression guidance
    text, the next-side first track, the MUST-be-next-side language, and
    the confidence cap. Also asserts the OLD "opposite side" / "flip to
    the other" framing is gone — the rename is part of the contract.

    The MUST language replaces the original PREFER wording. Live A/B test
    against Haiku (2026-05-22) showed PREFER produced 0/5 correct picks
    on a side-flip scenario; MUST produced 5/5 correct picks. See
    docs/features/llm-track-guess-side-progression-not-flip/.
    """
    p = _build_track_guess_prompt(
        **{
            **_SAMPLE_CALL_KWARGS,
            "likely_flip": True,
            "next_side_first": {
                "position": "B1", "title": "A Means to an End", "side": "B",
            },
        },
    )
    assert "likely_flip: true" in p
    assert "B1" in p
    assert "A Means to an End" in p
    # Hard MUST language — the live A/B test showed PREFER wasn't strong enough.
    assert "MUST be the NEXT SIDE" in p
    assert "next side" in p.lower()
    assert "progression" in p.lower()
    assert "FLIPPED THE RECORD" in p
    # Confidence cap so the LLM can't claim 'high' on a deterministically
    # ambiguous progression-vs-restart heartbeat.
    assert "Confidence MUST be at most 'medium'" in p
    # Old "opposite side" / "flipping to the other side" framing is gone.
    assert "opposite side" not in p.lower()
    assert "flipping to the other" not in p.lower()
    # Old "PREFER" language is gone — replaced by MUST per the A/B test.
    assert "PREFER the next side" not in p


def test_build_track_guess_prompt_omits_flip_guidance_when_false():
    """`likely_flip=False` → no flip text in the prompt (cache stability)."""
    p = _build_track_guess_prompt(**_SAMPLE_CALL_KWARGS)
    assert "likely_flip" not in p
    assert "flip" not in p.lower()


# ── Dead-air suppression gate ──────────────────────────────────────────


def _state_for_dead_air(*, deep: bool = True):
    """Build an Orchestrator + state that mirrors the end-of-side
    scenario: locked deep into side A, multi-heartbeat unmatched run.
    """
    from nowplaying.main import Orchestrator, State
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = llm_obj
    orch.state = State()
    locked_pos = "A4" if deep else "A1"
    orch.state.last_vinyl = {
        "artist": "Joy Division", "album": "Closer", "release_id": 12345,
        "side": "A", "track_position": locked_pos, "title": "X",
        "tracklist": [t for t in _TWO_SIDE_TRACKLIST if t["side"] == "A"],
    }
    orch._load_locked_tracks = lambda _state: list(_TWO_SIDE_TRACKLIST)
    return orch


def test_dead_air_gate_all_true_suppresses():
    """All four conditions hold → gate returns True."""
    orch = _state_for_dead_air(deep=True)
    orch.state.unmatched_streak = 3
    for v in (-37.0, -38.0, -39.0):  # below MUSIC_DB (-30) → dead air
        orch.state.recent_heartbeat_levels.append(v)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 90.0) is True


def test_dead_air_gate_fresh_audible_up_no_suppress():
    """Condition 1 fails (audible-up clock is fresh) → gate False."""
    orch = _state_for_dead_air(deep=True)
    orch.state.unmatched_streak = 3
    for v in (-37.0, -38.0, -39.0):  # below MUSIC_DB (-30) → dead air
        orch.state.recent_heartbeat_levels.append(v)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 30.0) is False


def test_dead_air_gate_short_unmatched_streak_no_suppress():
    """Condition 2 fails (one-off miss, not a multi-heartbeat run) → gate False."""
    orch = _state_for_dead_air(deep=True)
    orch.state.unmatched_streak = 1
    for v in (-37.0, -38.0, -39.0):  # below MUSIC_DB (-30) → dead air
        orch.state.recent_heartbeat_levels.append(v)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 90.0) is False


def test_dead_air_gate_music_level_audio_no_suppress():
    """Condition 3 fails (audio is music-level) → gate False."""
    orch = _state_for_dead_air(deep=True)
    orch.state.unmatched_streak = 3
    for v in (-2.0, -3.0, -1.0):
        orch.state.recent_heartbeat_levels.append(v)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 90.0) is False


def test_dead_air_gate_early_into_side_no_suppress():
    """Condition 4 fails (locked track is early on the side — mid-side miss,
    not end-of-side) → gate False. Structural guard against false-positives."""
    orch = _state_for_dead_air(deep=False)
    orch.state.unmatched_streak = 3
    for v in (-37.0, -38.0, -39.0):  # below MUSIC_DB (-30) → dead air
        orch.state.recent_heartbeat_levels.append(v)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 90.0) is False


def test_dead_air_gate_insufficient_level_samples_no_suppress():
    """Fewer than 3 heartbeat samples (cold start) → gate False — fail-safe."""
    orch = _state_for_dead_air(deep=True)
    orch.state.unmatched_streak = 3
    orch.state.recent_heartbeat_levels.append(-10.0)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 90.0) is False


def test_dead_air_gate_no_tracklist_no_suppress():
    """No catalog → gate False (can't evaluate depth)."""
    orch = _state_for_dead_air(deep=True)
    orch._load_locked_tracks = lambda _state: None
    orch.state.unmatched_streak = 3
    for v in (-37.0, -38.0, -39.0):  # below MUSIC_DB (-30) → dead air
        orch.state.recent_heartbeat_levels.append(v)
    assert orch._should_suppress_track_guess_for_dead_air(orch.state, 90.0) is False


def test_compute_track_guess_dead_air_skips_llm(keyed_env, caplog):
    """End-to-end: when the dead-air gate fires, _compute_track_guess
    returns None WITHOUT dispatching an LLM call, and pending_guess stays
    None (caller never assigns it). The suppression log line lands."""
    orch = _state_for_dead_air(deep=True)
    orch.state.unmatched_streak = 3
    for v in (-37.0, -38.0, -39.0):  # below MUSIC_DB (-30) → dead air
        orch.state.recent_heartbeat_levels.append(v)
    # Fake audible_up_at_mono so the elapsed clock reads >60s.
    import asyncio as _asyncio

    class _FakeLoop:
        def time(self) -> float:
            return 1000.0
    real_get = _asyncio.get_event_loop
    _asyncio.get_event_loop = lambda: _FakeLoop()  # type: ignore[assignment]
    orch.state.audible_up_at_mono = 800.0  # 200s ago
    try:
        fake_client = mock.MagicMock()
        fake_client.messages.create = mock.AsyncMock(
            side_effect=AssertionError("LLM must not be called when gate fires"),
        )
        orch.llm._client = fake_client
        with caplog.at_level(logging.INFO, logger="nowplaying.main"):
            g = _run(orch._compute_track_guess(orch.state))
    finally:
        _asyncio.get_event_loop = real_get  # type: ignore[assignment]
    assert g is None
    assert orch.state.pending_guess is None
    assert any(
        "track-guess: suppressed reason=dead_air" in r.getMessage()
        for r in caplog.records
    )


# ── End-of-side geometric gate ─────────────────────────────────────────


_SIDE_A_CUMULATIVE_S = sum(
    t["duration_seconds"] for t in _TWO_SIDE_TRACKLIST if t["side"] == "A"
)  # 1050s


class _FakeLoop:
    def __init__(self, t: float):
        self._t = t

    def time(self) -> float:
        return self._t


def _state_for_end_of_side(
    *,
    locked_pos: str = "A4",
    elapsed_s: float = 0.0,
    tracklist: list | None = None,
):
    """Locked at the last track of side A by default (A4 in
    `_TWO_SIDE_TRACKLIST`). Use ``locked_pos='A1'`` for the mid-side
    negative cases. ``elapsed_s`` controls the synthesized
    audible-up-clock for the geometric gate.
    """
    from nowplaying.main import Orchestrator, State
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = llm_obj
    orch.state = State()
    side_tracks = tracklist or _TWO_SIDE_TRACKLIST
    orch.state.last_vinyl = {
        "artist": "Joy Division", "album": "Closer", "release_id": 12345,
        "side": "A", "track_position": locked_pos, "title": "X",
        "tracklist": [t for t in side_tracks if t["side"] == "A"],
    }
    orch._load_locked_tracks = lambda _state: list(side_tracks)
    orch.state.audible_up_at_mono = 0.0
    orch.__test_loop = _FakeLoop(elapsed_s)  # type: ignore[attr-defined]
    return orch


def _with_fake_loop(orch, fn):
    """Patch asyncio.get_event_loop to return the orch's _FakeLoop for
    the duration of ``fn()`` — needed because
    ``_compute_elapsed_since_audible_up_s`` reads the loop clock."""
    import asyncio as _asyncio
    real_get = _asyncio.get_event_loop
    _asyncio.get_event_loop = lambda: orch.__test_loop  # type: ignore[assignment]
    try:
        return fn()
    finally:
        _asyncio.get_event_loop = real_get  # type: ignore[assignment]


def test_end_of_side_gate_past_side_duration_suppresses():
    """Locked at A4 (last on side) + elapsed past cumulative side
    duration + margin → gate fires regardless of streak (streak=0 here
    proves the gate doesn't depend on it)."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S + 15.0,
    )
    orch.state.unmatched_streak = 0
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is True


def test_end_of_side_gate_within_side_duration_no_suppress():
    """Last track but elapsed still within cumulative side duration AND
    audio is music-level → don't suppress; LLM should try to ID."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S - 50.0,
    )
    orch.state.unmatched_streak = 5
    for v in (-1.0, -1.5, -2.0):
        orch.state.recent_heartbeat_levels.append(v)
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_runout_noise_level_suppresses():
    """Locked at last-on-side, elapsed within cumulative duration (no
    geometric trigger), but level avg below the music floor (MUSIC_DB = -30)
    over last 3 heartbeats AND >= 1 unmatched → case B fires (runout groove
    at last-on-side treated as side-over)."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=100.0,  # still mid-side
    )
    orch.state.unmatched_streak = 1
    for v in (-33.0, -34.0, -35.0):  # below MUSIC_DB → runout
        orch.state.recent_heartbeat_levels.append(v)
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is True


def test_end_of_side_gate_runout_level_music_no_suppress():
    """Locked at last-on-side + unmatched but level avg is music-level
    (>= -3 dB) → don't suppress; might be a brief Shazam miss during
    actual music."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=100.0,
    )
    orch.state.unmatched_streak = 2
    for v in (-1.0, -2.5, -2.8):
        orch.state.recent_heartbeat_levels.append(v)
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_runout_zero_streak_no_suppress():
    """Locked at last-on-side + quiet level but streak=0 (just
    confirmed) → don't suppress; nothing's unmatched yet."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=100.0,
    )
    orch.state.unmatched_streak = 0
    for v in (-5.0, -5.0, -5.0):
        orch.state.recent_heartbeat_levels.append(v)
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_within_margin_no_suppress():
    """Just past cumulative duration but still inside the clock-drift
    margin → don't suppress yet."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S + 5.0,
    )
    orch.state.unmatched_streak = 5
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_mid_side_no_suppress():
    """Locked mid-side (A1) + elapsed > side duration → don't suppress;
    last-on-side is a precondition."""
    orch = _state_for_end_of_side(
        locked_pos="A1", elapsed_s=_SIDE_A_CUMULATIVE_S + 100.0,
    )
    orch.state.unmatched_streak = 5
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_no_catalog_no_suppress():
    """Catalog miss → can't evaluate geometry → fail-safe to False."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S + 100.0,
    )
    orch._load_locked_tracks = lambda _state: None
    orch.state.unmatched_streak = 3
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_missing_lock_no_suppress():
    """No lock at all → False."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S + 100.0,
    )
    orch.state.last_vinyl = None
    orch.state.unmatched_streak = 3
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False


def test_end_of_side_gate_fallback_missing_durations():
    """When tracklist durations are missing the geometric check can't
    fire — fall back to the streak signal (>=1 unmatched). Releases like
    Hum *Downward Is Heavenward* hit this path."""
    no_durations = [
        {"track_position": t["track_position"], "side": t["side"],
         "title": t["title"], "duration_seconds": None}
        for t in _TWO_SIDE_TRACKLIST
    ]
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=0.0, tracklist=no_durations,
    )
    orch.state.unmatched_streak = 0
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is False
    orch.state.unmatched_streak = 1
    assert _with_fake_loop(
        orch,
        lambda: orch._should_suppress_track_guess_for_end_of_side(orch.state),
    ) is True


def test_compute_track_guess_end_of_side_skips_llm(keyed_env, caplog):
    """End-to-end: locked at last track + elapsed past side duration →
    no LLM call, no guess returned, suppression log lands. This is the
    live regression captured from 2026-05-26 14:23–14:24 logs."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S + 50.0,
    )
    orch.state.unmatched_streak = 0  # streak independence is the point
    # Audio level high (mimics observed runout: level_db ~-0.7) so the
    # dead-air gate stays *off* — proves end-of-side gate fires on its own.
    for v in (-0.7, -0.7, -0.7):
        orch.state.recent_heartbeat_levels.append(v)
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=AssertionError("LLM must not be called at end-of-side"),
    )
    orch.llm._client = fake_client
    with caplog.at_level(logging.INFO, logger="nowplaying.main"):
        g = _with_fake_loop(orch, lambda: _run(orch._compute_track_guess(orch.state)))
    assert g is None
    assert any(
        "track-guess: suppressed reason=end_of_side" in r.getMessage()
        for r in caplog.records
    )


def test_compute_track_guess_end_of_side_clears_stale_pending_guess(keyed_env):
    """End-of-side suppression must also drop any stale ``pending_guess``
    left over from a prior heartbeat. Without this, the NEEDS_ID path's
    ``_try_publish_guess_as_predicted`` republishes the old guess as a
    predicted-advance and the kiosk keeps showing a track that's over.

    Live regression: Foo Fighters Wasting Light 2026-05-26 16:35:02 set
    pending_guess = Miss The Misery (C3); 16:35:18 the suppression fired
    but pending_guess survived and routed back through predicted-advance
    publish, restarting the kiosk timer for a runout track."""
    orch = _state_for_end_of_side(
        locked_pos="A4", elapsed_s=_SIDE_A_CUMULATIVE_S + 50.0,
    )
    orch.state.pending_guess = {
        "position": "A4", "title": "Colony",
        "confidence": "high", "source": "llm",
    }
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        side_effect=AssertionError("LLM must not be called at end-of-side"),
    )
    orch.llm._client = fake_client
    g = _with_fake_loop(orch, lambda: _run(orch._compute_track_guess(orch.state)))
    assert g is None
    assert orch.state.pending_guess is None


# ── Position-ordinal fallback when durations are missing ───────────────────


# Tracklist mirroring Hum *Downward Is Heavenward* — 2-LP, all durations NULL.
_HUM_TRACKLIST = [
    {"track_position": "A1", "side": "A", "title": "Isle Of The Cheetah", "duration_seconds": None},
    {"track_position": "A2", "side": "A", "title": "Comin Home", "duration_seconds": None},
    {"track_position": "A3", "side": "A", "title": "If You Are To Bloom", "duration_seconds": None},
    {"track_position": "A4", "side": "A", "title": "Ms. Lazarus", "duration_seconds": None},
    {"track_position": "B1", "side": "B", "title": "Afternoon With The Axolotls", "duration_seconds": None},
    {"track_position": "B2", "side": "B", "title": "Green To Me", "duration_seconds": None},
    {"track_position": "B3", "side": "B", "title": "Dreamboat", "duration_seconds": None},
    {"track_position": "C1", "side": "C", "title": "The Inuit Promise", "duration_seconds": None},
    {"track_position": "D1", "side": "D", "title": "Puppets", "duration_seconds": None},
    {"track_position": "D2", "side": "D", "title": "Aphids", "duration_seconds": None},
    {"track_position": "D3", "side": "D", "title": "Boy With Stick", "duration_seconds": None},
]


def _orch_with_nullduration_catalog(*, locked_pos: str, locked_side: str):
    from nowplaying.main import Orchestrator, State
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    orch = Orchestrator.__new__(Orchestrator)
    orch.llm = llm_obj
    orch.state = State()
    orch.state.last_vinyl = {
        "artist": "Hum", "album": "Downward Is Heavenward",
        "release_id": 29155441, "side": locked_side,
        "track_position": locked_pos, "title": "X",
    }
    orch._load_locked_tracks = lambda _state: list(_HUM_TRACKLIST)
    return orch


def test_pending_guess_dropped_when_position_disagrees_with_payload():
    """Regression for 2026-05-22 Hum YPAA side-flip dual-display:
    LLM track-guess produced position=A1, but predicted-advance had
    already advanced to B6 via the streak path. Attaching the stale
    A1 guess to the B6 publish caused a BEST GUESS card for Little
    Dipper to render ON TOP of the Why I Like The Robins track surface.

    The fix: drop the guess silently when its position disagrees with
    the published track_position. The guess is only meaningful when
    it agrees with what we're publishing.
    """
    orch = _make_orch(llm_enabled=False)
    orch.state.pending_guess = {
        "position": "A1",
        "title": "Little Dipper",
        "confidence": "medium",
        "source": "llm",
    }
    payload = {
        "ts": "2026-05-22T19:47:13Z",
        "state": "PLAYING",
        "source": "vinyl",
        "title": "Why I Like The Robins",
        "track_position": "B6",        # ← predicted-advance picked B6
        "match_method": "predicted",
    }
    result = orch._anchor_and_publish(payload)
    assert "guess" not in result, (
        "stale pending_guess must NOT be attached when it disagrees with "
        "the published track_position"
    )
    assert orch.state.pending_guess is None, "must still clear after consumption"


def test_pending_guess_attached_when_position_agrees_with_payload():
    """Companion to the disagreement test: when the LLM's track-guess
    drove the predicted-advance (state-decay → _try_publish_guess_as_predicted),
    the published position matches the guess's position and the guess
    SHOULD attach so the kiosk renders the BEST GUESS overlay.
    """
    orch = _make_orch(llm_enabled=False)
    orch.state.pending_guess = {
        "position": "B6",
        "title": "Why I Like The Robins",
        "confidence": "medium",
        "source": "llm",
    }
    payload = {
        "ts": "2026-05-22T19:47:13Z",
        "state": "PLAYING",
        "source": "vinyl",
        "title": "Why I Like The Robins",
        "track_position": "B6",
        "match_method": "predicted",
    }
    result = orch._anchor_and_publish(payload)
    assert result.get("guess") is not None
    assert result["guess"]["position"] == "B6"
