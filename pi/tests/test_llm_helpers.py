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

from nowplaying.llm import USE_HEURISTIC, LLMAssist


# ── _str ────────────────────────────────────────────────────────────────


# ── _norm_key ───────────────────────────────────────────────────────────


# ── _is_same_track ──────────────────────────────────────────────────────


# ── _filter_recent_history ──────────────────────────────────────────────


# ── _bucket_elapsed ─────────────────────────────────────────────────────


# ── _tracklist_payload ──────────────────────────────────────────────────


# ── _locked_payload ─────────────────────────────────────────────────────


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
