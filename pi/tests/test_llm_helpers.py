"""Unit tests for helpers extracted during the Phase B-3 LLM cleanup.

Covers the pure helpers split out of `_build_track_guess_prompt`
(`_norm_key`, `_is_same_track`, `_filter_recent_history`,
`_bucket_elapsed`, `_tracklist_payload`, `_locked_payload`) and the
`LLMAssist._call_tool` helper extracted out of `_invoke`'s try-block.

These helpers are private but stable — they exist so the public
prompt-builder and `_invoke` stay simple and so individual concerns
(history filtering, elapsed bucketing, payload shaping) can be
exercised in isolation without re-asserting the full prompt string.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.llm import USE_HEURISTIC, LLMAssist
from nowplaying.llm.track_guess import (
    _bucket_elapsed,
    _filter_recent_history,
    _is_same_track,
    _locked_payload,
    _norm_key,
    _str,
    _tracklist_payload,
)


# ── _str ────────────────────────────────────────────────────────────────


def test_str_passes_through_none():
    assert _str(None) is None


@pytest.mark.parametrize("v", [0, 1, 3.14, True, False])
def test_str_passes_through_numerics_and_bools(v):
    # JSON-serialization correctness depends on preserving int/float/bool
    # rather than stringifying them.
    assert _str(v) is v


def test_str_stringifies_everything_else():
    assert _str({"a": 1}) == "{'a': 1}"
    assert _str([1, 2]) == "[1, 2]"


# ── _norm_key ───────────────────────────────────────────────────────────


def test_norm_key_strips_and_casefolds():
    assert _norm_key("  Beatles  ") == "beatles"


def test_norm_key_handles_none_and_non_string():
    assert _norm_key(None) == ""
    assert _norm_key(123) == ""


# ── _is_same_track ──────────────────────────────────────────────────────


def test_is_same_track_matches_on_normalized_fields():
    assert _is_same_track(
        {"artist": " BEATLES ", "title": "Help!"}, "beatles", "help!",
    )


def test_is_same_track_false_when_current_empty():
    # Defense: an empty locked_ctx must NOT drop every history row.
    assert not _is_same_track(
        {"artist": "Beatles", "title": "Help!"}, "", "",
    )


def test_is_same_track_false_on_mismatch():
    assert not _is_same_track(
        {"artist": "Beatles", "title": "Yesterday"}, "beatles", "help!",
    )


# ── _filter_recent_history ──────────────────────────────────────────────


def test_filter_recent_history_drops_current_track():
    history = [
        {"artist": "Beatles", "title": "Help!"},
        {"artist": "Beatles", "title": "Yesterday"},
    ]
    locked = {"locked_artist": "Beatles", "locked_title": "Help!"}
    out = _filter_recent_history(history, locked)
    assert out == [{"artist": "Beatles", "title": "Yesterday"}]


def test_filter_recent_history_handles_none():
    assert _filter_recent_history(None, {"locked_artist": "a", "locked_title": "b"}) == []


def test_filter_recent_history_keeps_all_when_current_empty():
    # Empty locked_ctx means "no current track" — keep everything.
    history = [{"artist": "Beatles", "title": "Help!"}]
    out = _filter_recent_history(history, {})
    assert out == [{"artist": "Beatles", "title": "Help!"}]


# ── _bucket_elapsed ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "elapsed,expected",
    [(0.0, 0), (2.4, 0), (2.5, 0), (3.0, 5), (47.0, 45), (49.0, 50), (50.0, 50)],
)
def test_bucket_elapsed(elapsed, expected):
    # banker's rounding via round(): 2.5 → 0 (round half to even).
    assert _bucket_elapsed(elapsed) == expected


# ── _tracklist_payload ──────────────────────────────────────────────────


def test_tracklist_payload_prefers_track_position():
    out = _tracklist_payload([
        {"track_position": "A1", "position": "X1", "title": "Side A Opener"},
    ])
    assert out == [{
        "position": "A1", "title": "Side A Opener",
        "duration_s": None, "start_s": 0, "end_s": None,
    }]


def test_tracklist_payload_falls_back_to_position():
    out = _tracklist_payload([{"position": "B2", "title": "Track"}])
    assert out[0]["position"] == "B2"


def test_tracklist_payload_cumulative_windows():
    """start_s/end_s accumulate from the side's start so the model can locate
    the track by window lookup instead of summing durations itself."""
    out = _tracklist_payload([
        {"position": "A1", "title": "One", "duration_seconds": 190},
        {"position": "A2", "title": "Two", "duration_seconds": 177},
        {"position": "A3", "title": "Three", "duration_seconds": 48},
    ])
    assert [(t["start_s"], t["end_s"]) for t in out] == [
        (0, 190), (190, 367), (367, 415),
    ]


def test_tracklist_payload_missing_duration_breaks_window():
    """A track with unknown duration gets end_s=None and stops the running
    total from advancing past it."""
    out = _tracklist_payload([
        {"position": "A1", "title": "One", "duration_seconds": 100},
        {"position": "A2", "title": "Two"},               # unknown duration
        {"position": "A3", "title": "Three", "duration_seconds": 50},
    ])
    assert (out[0]["start_s"], out[0]["end_s"]) == (0, 100)
    assert (out[1]["start_s"], out[1]["end_s"]) == (100, None)
    assert (out[2]["start_s"], out[2]["end_s"]) == (100, 150)


def test_tracklist_payload_duration_priority():
    # duration_seconds (catalog key) wins over legacy aliases.
    out = _tracklist_payload([{
        "track_position": "A1", "title": "x",
        "duration_seconds": 180, "duration_s": 200, "duration": 220,
    }])
    assert out[0]["duration_s"] == 180


# ── _locked_payload ─────────────────────────────────────────────────────


def test_locked_payload_renames_keys():
    out = _locked_payload({
        "locked_artist": "Beatles",
        "locked_album": "Help!",
        "locked_release_id": 123,
        "locked_side": "A",
        "locked_title": "Help!",
    })
    assert out == {
        "artist": "Beatles",
        "album": "Help!",
        "release_id": 123,
        "side": "A",
        "last_confirmed_track": "Help!",
    }


def test_locked_payload_renders_missing_as_none():
    out = _locked_payload({})
    assert all(v is None for v in out.values())


# ── LLMAssist._call_tool ────────────────────────────────────────────────


def test_call_tool_scaffold_path_returns_use_heuristic(monkeypatch):
    """When tool_spec is None, _call_tool constructs the client (proves
    configuration) and returns USE_HEURISTIC without making a request."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-xyz")
    assist = LLMAssist()
    fake_client = mock.Mock()
    assist._client = fake_client

    result = asyncio.run(assist._call_tool("prompt", object, None))

    assert result is USE_HEURISTIC
    # Scaffold path must NOT call messages.create.
    fake_client.messages.create.assert_not_called()


def test_call_tool_happy_path(monkeypatch):
    """With a tool_spec, _call_tool issues a messages.create call,
    extracts the tool_use input, and calls the schema with **kwargs."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-xyz")
    assist = LLMAssist()

    # Fake AsyncAnthropic response shape.
    tool_block = mock.Mock()
    tool_block.type = "tool_use"
    tool_block.name = "my_tool"
    tool_block.input = {"accept": True, "reason": "ok"}
    response = mock.Mock()
    response.content = [tool_block]

    fake_client = mock.Mock()
    fake_client.messages.create = mock.AsyncMock(return_value=response)
    assist._client = fake_client

    schema = mock.Mock(return_value="VERDICT")
    tool_spec = {"name": "my_tool"}

    result = asyncio.run(assist._call_tool("prompt", schema, tool_spec))

    assert result == "VERDICT"
    schema.assert_called_once_with(accept=True, reason="ok")
    fake_client.messages.create.assert_awaited_once()
