"""F7: Tests for the LLM-release-picker hook on `search_collection` and
the real `rank_releases` implementation."""
from __future__ import annotations

import asyncio
import logging
from unittest import mock

import pytest

from nowplaying import llm as llm_mod
from nowplaying.llm import LLMAssist, ReleaseRanking, USE_HEURISTIC


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


# ── rank_releases unit tests ─────────────────────────────────────────────


def test_rank_releases_returns_ranking(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "rank_releases",
            {"ranked_release_ids": [33, 11, 22], "reason": "same-artist preference"},
        ),
    )
    a._client = fake_client
    verdict = _run(a.rank_releases(
        [
            {"release_id": 11, "artist": "X"},
            {"release_id": 22, "artist": "Y"},
            {"release_id": 33, "artist": "X"},
        ],
        {"query": "x", "locked_artist": "X", "locked_release_id": None},
    ))
    assert isinstance(verdict, ReleaseRanking)
    assert verdict.release_ids == (33, 11, 22)
    assert verdict.reason == "same-artist preference"
    assert verdict.release_id == 33  # backward-compat property


def test_rank_releases_cleans_non_int_ids(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "rank_releases",
            {"ranked_release_ids": [11, "not-a-number", 22, None], "reason": "x"},
        ),
    )
    a._client = fake_client
    verdict = _run(a.rank_releases([{"release_id": 11}, {"release_id": 22}], {"query": "x"}))
    assert verdict.release_ids == (11, 22)


def test_rank_releases_malformed_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    a = LLMAssist()
    bad = mock.MagicMock()
    bad.content = []
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(return_value=bad)
    a._client = fake_client
    result = _run(a.rank_releases([{"release_id": 1}], {"query": "q"}))
    assert result is USE_HEURISTIC


def test_rank_releases_disabled_returns_sentinel(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = LLMAssist()
    assert _run(a.rank_releases([], {})) is USE_HEURISTIC


def test_release_ranking_property_empty_list():
    """release_id property returns None when the LLM produced no ids."""
    r = ReleaseRanking(release_ids=())
    assert r.release_id is None


# ── search_collection endpoint integration ───────────────────────────────


def _make_request(llm_obj=None, state_obj=None) -> mock.MagicMock:
    req = mock.MagicMock()
    app = {}
    if llm_obj is not None:
        app["llm"] = llm_obj
    if state_obj is not None:
        app["state"] = state_obj
    req.app = app
    return req


def test_rerank_passthrough_when_llm_disabled():
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = False
    req = _make_request(llm_obj=llm_obj)
    items = [{"release_id": 1}, {"release_id": 2}]
    result = _run(_maybe_llm_rerank_items(req, "query", items))
    assert result == items


def test_rerank_passthrough_when_single_item():
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    llm_obj.rank_releases = mock.AsyncMock(side_effect=AssertionError("must not call"))
    req = _make_request(llm_obj=llm_obj)
    items = [{"release_id": 1}]
    result = _run(_maybe_llm_rerank_items(req, "query", items))
    assert result == items


def test_rerank_reorders_top_n_per_llm():
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    llm_obj.rank_releases = mock.AsyncMock(
        return_value=ReleaseRanking(release_ids=(3, 1), reason="picks"),
    )
    req = _make_request(llm_obj=llm_obj)
    items = [{"release_id": i} for i in (1, 2, 3, 4)]
    result = _run(_maybe_llm_rerank_items(req, "query", items))
    # 3 first (LLM put it first), then 1, then 2 (heuristic order for
    # candidates the LLM didn't mention), then 4 (was in top 10 also, but
    # also unmentioned; same bucket as 2).
    assert [r["release_id"] for r in result] == [3, 1, 2, 4]


def test_rerank_ignores_unknown_release_ids():
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    llm_obj.rank_releases = mock.AsyncMock(
        return_value=ReleaseRanking(release_ids=(99, 2, 100), reason="bad"),
    )
    req = _make_request(llm_obj=llm_obj)
    items = [{"release_id": 1}, {"release_id": 2}, {"release_id": 3}]
    result = _run(_maybe_llm_rerank_items(req, "query", items))
    # 2 lifted to front (only valid id), then 1 and 3 in original order.
    assert [r["release_id"] for r in result] == [2, 1, 3]


def test_rerank_keeps_heuristic_when_all_ids_unknown():
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    llm_obj.rank_releases = mock.AsyncMock(
        return_value=ReleaseRanking(release_ids=(99, 100), reason="hallucinated"),
    )
    req = _make_request(llm_obj=llm_obj)
    items = [{"release_id": 1}, {"release_id": 2}]
    result = _run(_maybe_llm_rerank_items(req, "query", items))
    assert result == items  # unchanged


def test_rerank_recovers_on_llm_exception(caplog):
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    llm_obj.rank_releases = mock.AsyncMock(side_effect=RuntimeError("boom"))
    req = _make_request(llm_obj=llm_obj)
    items = [{"release_id": 1}, {"release_id": 2}]
    with caplog.at_level(logging.WARNING, logger="nowplaying.control"):
        result = _run(_maybe_llm_rerank_items(req, "query", items))
    assert result == items
    assert any("rank_releases" in r.getMessage() for r in caplog.records)


def test_rerank_null_safe_state():
    """When state.last_vinyl is None (nothing playing), the hook must not crash."""
    from nowplaying.control import _maybe_llm_rerank_items
    llm_obj = LLMAssist()
    llm_obj.enabled = True
    captured_ctx = {}

    async def capture(candidates, ctx):
        captured_ctx.update(ctx)
        return ReleaseRanking(release_ids=(1,), reason="x")

    llm_obj.rank_releases = capture
    state = mock.MagicMock()
    state.last_vinyl = None
    req = _make_request(llm_obj=llm_obj, state_obj=state)
    items = [{"release_id": 1}, {"release_id": 2}]
    _run(_maybe_llm_rerank_items(req, "query", items))
    assert captured_ctx["locked_release_id"] is None
    assert captured_ctx["locked_artist"] is None


# ── Context enrichment (Bug #8: rank-releases-locked-album-priority) ──────


def test_prompt_includes_locked_album_title_not_just_release_id():
    """Regression for the Beatles "something" search where the LLM saw
    only locked_release_id=4042258 (opaque int) and couldn't connect it
    to a candidate by name. With the album title in context, the model
    can reason about candidates semantically.
    """
    from nowplaying.llm.release import _build_rank_releases_prompt

    ctx = {
        "query": "something",
        "locked_release_id": 4042258,
        "locked_album_title": "Abbey Road",
        "locked_artist": "The Beatles",
        "locked_track_position": "A2",
        "locked_track_title": "Something",
    }
    candidates = [
        {"release_id": 28859359, "artist": "The Beatles", "title": "1967-1970",
         "tracks": [{"position": "A1", "title": "Strawberry Fields Forever"}]},
        {"release_id": 4042258, "artist": "The Beatles", "title": "Abbey Road",
         "tracks": [{"position": "A2", "title": "Something"}]},
    ]
    prompt = _build_rank_releases_prompt(candidates, ctx)
    # Locked album title appears so the model can identify the locked candidate by name.
    assert "Abbey Road" in prompt
    assert '"locked_album_title": "Abbey Road"' in prompt or '"Abbey Road"' in prompt
    # Just-played track context appears.
    assert "Something" in prompt  # track title also present
    assert "A2" in prompt  # position


def test_prompt_marks_candidates_with_matching_tracks():
    """has_matching_track is computed per candidate from the candidate's
    tracklist + the search query. Strong signal for "the user is asking
    about THIS release."
    """
    from nowplaying.llm.release import _build_rank_releases_prompt

    ctx = {
        "query": "something",
        "locked_release_id": 4042258,
        "locked_album_title": "Abbey Road",
        "locked_artist": "The Beatles",
    }
    candidates = [
        # Abbey Road — has Something on A2
        {"release_id": 4042258, "artist": "The Beatles", "title": "Abbey Road",
         "tracks": [{"position": "A2", "title": "Something"}]},
        # Some unrelated Nirvana album — no Something track
        {"release_id": 4819086, "artist": "Nirvana", "title": "Nevermind",
         "tracks": [{"position": "A1", "title": "Smells Like Teen Spirit"}]},
    ]
    prompt = _build_rank_releases_prompt(candidates, ctx)
    # Abbey Road's payload should have has_matching_track=true
    # Nirvana's should have has_matching_track=false
    # Both serialized in the candidates JSON inside the prompt.
    import json
    # Extract the candidates JSON line (last line in the prompt).
    candidates_line = [
        line for line in prompt.split("\n") if line.startswith("Candidates: ")
    ][0]
    candidates_json = json.loads(candidates_line.removeprefix("Candidates: "))
    by_rid = {c["release_id"]: c for c in candidates_json}
    assert by_rid[4042258]["has_matching_track"] is True
    assert by_rid[4819086]["has_matching_track"] is False


def test_prompt_has_guidance_about_locked_album_when_track_matches():
    """The prompt text explicitly tells the model that when the query
    matches a track on the locked album AND on other candidates, the
    locked album is the answer. This is the load-bearing coaching."""
    from nowplaying.llm.release import _build_rank_releases_prompt

    ctx = {"query": "something", "locked_release_id": 1, "locked_album_title": "X"}
    prompt = _build_rank_releases_prompt([], ctx)
    # Guidance language — checking for the key concept words.
    assert "locked album" in prompt.lower()
    assert "has_matching_track" in prompt
    assert "right now" in prompt.lower()  # "what's on the turntable RIGHT NOW"


def test_build_rerank_ctx_includes_all_locked_fields():
    """The orchestrator-side ctx builder must populate all 6 fields
    the prompt expects — no silently-missing keys."""
    from nowplaying.control.search import _build_rerank_ctx

    class _State:
        last_vinyl = {
            "release_id": 4042258,
            "artist": "The Beatles",
            "album": "Abbey Road",
            "title": "Something",
            "track_position": "A2",
        }

    ctx = _build_rerank_ctx("something", _State())
    assert ctx["query"] == "something"
    assert ctx["locked_release_id"] == 4042258
    assert ctx["locked_artist"] == "The Beatles"
    assert ctx["locked_album_title"] == "Abbey Road"
    assert ctx["locked_track_position"] == "A2"
    assert ctx["locked_track_title"] == "Something"


def test_build_rerank_ctx_handles_no_lock():
    """When state.last_vinyl is None (vinyl idle), all 5 locked_* fields
    are None — model sees a query-only context."""
    from nowplaying.control.search import _build_rerank_ctx

    class _State:
        last_vinyl = None

    ctx = _build_rerank_ctx("something", _State())
    assert ctx["query"] == "something"
    assert ctx["locked_release_id"] is None
    assert ctx["locked_artist"] is None
    assert ctx["locked_album_title"] is None
    assert ctx["locked_track_position"] is None
    assert ctx["locked_track_title"] is None


# ── judge_reverse_lookup (Bug #9) ─────────────────────────────────────────


def test_judge_reverse_lookup_disabled_returns_sentinel(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = LLMAssist()
    result = _run(a.judge_reverse_lookup({"release_id": 1}, [], {}))
    assert result is USE_HEURISTIC


def test_judge_reverse_lookup_returns_pick(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from nowplaying.llm.release import ReleasePick
    a = LLMAssist()
    fake_client = mock.MagicMock()
    fake_client.messages.create = mock.AsyncMock(
        return_value=_fake_tool_use_response(
            "judge_reverse_lookup",
            {"release_id": 42, "reason": "recent history shows flip"},
        ),
    )
    a._client = fake_client
    verdict = _run(a.judge_reverse_lookup(
        winner={"release_id": 1, "album": "Old", "artist": "X"},
        alternates=[{"release_id": 42, "album": "New", "artist": "X"}],
        ctx={"query_artist": "X", "query_title": "Song"},
    ))
    assert isinstance(verdict, ReleasePick)
    assert verdict.release_id == 42
    assert verdict.reason == "recent history shows flip"


def test_parse_release_pick_requires_valid_int():
    from nowplaying.llm.release import _parse_release_pick
    import pytest
    with pytest.raises(ValueError):
        _parse_release_pick(release_id="not-a-number", reason="x")


def test_build_reverse_lookup_prompt_includes_flip_signals():
    """The prompt must surface the signals that distinguish a flip from
    a same-album reconfirm: recent history, time gap, locked album by
    name, just-played track."""
    from nowplaying.llm.release import _build_reverse_lookup_prompt

    winner = {
        "release_id": 4042258,
        "album": "Abbey Road",
        "artist": "The Beatles",
        "matched_track_position": "A2",
        "matched_track_title": "Something",
        "score": 100,
    }
    alternates = [
        {
            "release_id": 28859305,
            "album": "1962-1966",
            "artist": "The Beatles",
            "matched_track_position": "A3",
            "matched_track_title": "Something",
            "score": 85,
        },
    ]
    ctx = {
        "query_artist": "The Beatles",
        "query_title": "Something",
        "query_isrc": "GBAYE0900001",
        "locked_release_id": 4042258,
        "locked_album_title": "Abbey Road",
        "locked_artist": "The Beatles",
        "locked_track_position": "A2",
        "locked_track_title": "Something",
        "seconds_since_last_confirm": 14.7,
        "recent_history": [
            {"artist": "The Beatles", "title": "Come Together", "release_id": 4042258},
            {"artist": "The Beatles", "title": "Maxwell's Silver Hammer", "release_id": 4042258},
        ],
    }
    p = _build_reverse_lookup_prompt(winner, alternates, ctx)
    # Locked album by name (so the model can identify candidates semantically).
    assert "Abbey Road" in p
    # Just-played track context.
    assert "Something" in p
    # Recent history present.
    assert "Come Together" in p
    assert "Maxwell" in p
    # Flip-signal language ("flipped to a different record" / "time gap").
    assert "flip" in p.lower() or "FLIP" in p
    # Time-since-confirm is bucketed (14.7s → 15)
    assert "15" in p


def test_build_reverse_lookup_prompt_buckets_seconds_since_confirm():
    """seconds_since_last_confirm rounds to 5s buckets so prompts hash
    identically across nearby heartbeats."""
    from nowplaying.llm.release import _build_reverse_lookup_prompt

    winner = {"release_id": 1, "album": "A", "artist": "X"}
    base_ctx = {"query_artist": "X", "query_title": "Song", "locked_release_id": 1}

    p_13 = _build_reverse_lookup_prompt(winner, [], {**base_ctx, "seconds_since_last_confirm": 13.0})
    p_14 = _build_reverse_lookup_prompt(winner, [], {**base_ctx, "seconds_since_last_confirm": 14.0})
    p_15 = _build_reverse_lookup_prompt(winner, [], {**base_ctx, "seconds_since_last_confirm": 15.0})
    p_17 = _build_reverse_lookup_prompt(winner, [], {**base_ctx, "seconds_since_last_confirm": 17.0})
    assert p_13 == p_14 == p_15 == p_17, "13-17s should all bucket to 15"

    p_20 = _build_reverse_lookup_prompt(winner, [], {**base_ctx, "seconds_since_last_confirm": 20.0})
    assert p_20 != p_15
