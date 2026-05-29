from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

import asyncio
from unittest import mock

from nowplaying.llm import LLMAssist, USE_HEURISTIC
from nowplaying.llm.title_clean import (
    CleanTitle,
    _CLEAN_TITLE_TOOL_SPEC,
    _build_clean_title_prompt,
)


def _run(coro):
    return asyncio.run(coro)


def test_tool_spec_shape():
    assert _CLEAN_TITLE_TOOL_SPEC["name"] == "clean_track_title"
    assert "clean_title" in _CLEAN_TITLE_TOOL_SPEC["input_schema"]["properties"]


def test_prompt_includes_raw_and_rules():
    p = _build_clean_title_prompt("Penny Lane (2017 Mix)")
    assert "Penny Lane (2017 Mix)" in p
    assert "Live" in p
    # Few-shot examples must be present so Haiku sees the exact failure cases.
    assert "-> 'Penny Lane'" in p
    assert "Strawberry Fields Forever" in p
    assert "I Am The Walrus" in p
    assert "Revolution (2025 Mix)" in p


def test_disabled_returns_heuristic():
    a = LLMAssist()
    a.enabled = False
    assert _run(a.clean_track_title("Penny Lane (2017 Mix)")) is USE_HEURISTIC


def test_enabled_parses_verdict():
    a = LLMAssist()
    a.enabled = True

    async def fake_call_tool(prompt, schema, tool_spec):
        return CleanTitle(clean_title="Penny Lane")

    with mock.patch.object(a, "_call_tool", side_effect=fake_call_tool):
        verdict = _run(a.clean_track_title("Penny Lane (2017 Mix)"))
    assert isinstance(verdict, CleanTitle)
    assert verdict.clean_title == "Penny Lane"


def test_error_falls_back_to_heuristic():
    a = LLMAssist()
    a.enabled = True

    async def boom(prompt, schema, tool_spec):
        raise RuntimeError("api down")

    with mock.patch.object(a, "_call_tool", side_effect=boom):
        assert _run(a.clean_track_title("Penny Lane (2017 Mix)")) is USE_HEURISTIC
