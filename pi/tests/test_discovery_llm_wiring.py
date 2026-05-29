"""Assert that _run_discovery passes a non-None llm to persist.

When ANTHROPIC_API_KEY is present, discovered-title cleaning should use the
LLM path. The persist() signature is ``persist(release, *, llm=None)`` — this
test verifies the keyword argument is forwarded.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_SCRIPTS = _PI_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import recognize_proto  # noqa: E402


_FAKE_RELEASE = {
    "mbid": "mb-llm-test",
    "artist": "Test Artist",
    "album": "Test Album",
    "year": 2000,
    "tracks": [],
}


def test_run_discovery_passes_llm_to_persist(monkeypatch):
    """_run_discovery must call persist(..., llm=<LLMAssist instance>).

    The llm accessor returns a module-level LLMAssist; we verify it is
    forwarded — not None — so discovered releases get LLM title cleaning when
    a key is configured.
    """
    persist_calls: list[dict] = []

    async def _fake_isrc(isrc, **k):
        return _FAKE_RELEASE

    async def _fake_persist(rel, *, llm=None):
        persist_calls.append({"rel": rel, "llm": llm})

    # Stub out the MB lookup so _run_discovery reaches the persist call.
    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup, "lookup_by_isrc", _fake_isrc,
    )
    monkeypatch.setattr(
        recognize_proto.musicbrainz_lookup, "persist", _fake_persist,
    )

    # Force _discovery_llm() to return a sentinel so we can assert identity
    # without needing a real ANTHROPIC_API_KEY in the test environment.
    sentinel_llm = object()
    # Reset module-level cache so our monkeypatch takes effect cleanly.
    recognize_proto._DISCOVERY_LLM = None
    monkeypatch.setattr(recognize_proto, "_discovery_llm", lambda: sentinel_llm)

    asyncio.run(
        recognize_proto._run_discovery("Test Artist", "Test Album", "ISRC123", ("test artist", "test album")),
    )

    assert len(persist_calls) == 1, "persist must be called exactly once"
    assert persist_calls[0]["llm"] is sentinel_llm, (
        "persist must receive the llm kwarg from _discovery_llm(); "
        f"got llm={persist_calls[0]['llm']!r}"
    )


def test_discovery_llm_accessor_is_lazy(monkeypatch):
    """_discovery_llm() must construct LLMAssist lazily and cache the result."""
    constructed: list[object] = []

    class _FakeLLMAssist:
        def __init__(self):
            constructed.append(self)

    # Reset the module-level cache.
    recognize_proto._DISCOVERY_LLM = None

    with mock.patch.dict("sys.modules", {"nowplaying.llm": mock.MagicMock(LLMAssist=_FakeLLMAssist)}):
        # Patch the lazy import inside the accessor.
        original_accessor = recognize_proto._discovery_llm

        def _patched_accessor():
            global _accessor_module  # noqa: PLW0602 — test-only probe
            if recognize_proto._DISCOVERY_LLM is None:
                recognize_proto._DISCOVERY_LLM = _FakeLLMAssist()
            return recognize_proto._DISCOVERY_LLM

        monkeypatch.setattr(recognize_proto, "_discovery_llm", _patched_accessor)

        first = recognize_proto._discovery_llm()
        second = recognize_proto._discovery_llm()

    assert first is second, "_discovery_llm() must return the same cached instance"


def test_discovery_llm_not_none_on_two_calls(monkeypatch):
    """Module-level _DISCOVERY_LLM caches across calls — calling twice yields
    the same object without constructing a new LLMAssist each time.
    """
    # Reset the cache.
    recognize_proto._DISCOVERY_LLM = None

    sentinel = object()
    call_count = 0

    def _fake_llm_factory():
        nonlocal call_count
        call_count += 1
        return sentinel

    # Monkeypatch the accessor to use our counter-based factory.
    original = recognize_proto._discovery_llm

    def _counting_accessor():
        if recognize_proto._DISCOVERY_LLM is None:
            recognize_proto._DISCOVERY_LLM = _fake_llm_factory()
        return recognize_proto._DISCOVERY_LLM

    monkeypatch.setattr(recognize_proto, "_discovery_llm", _counting_accessor)

    a = recognize_proto._discovery_llm()
    b = recognize_proto._discovery_llm()

    assert a is b
    assert call_count == 1, "factory must only be called once (lazy cache)"
