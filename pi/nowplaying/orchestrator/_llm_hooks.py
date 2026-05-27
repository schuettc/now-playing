"""LLMHooksMixin — LLM-assisted track-change and guess decisions.

This file is now a composition shim.  The implementation lives under
``nowplaying/orchestrator/llm/``:

  _reverse_lookup.py  — reverse-lookup disambiguation
  _shazam_relevance.py — F5 Shazam relevance filter
  _track_change.py    — Rule A override + context recorders
  _track_guess.py     — track-guess proposer + side-flip detection

Re-exports at the bottom preserve backward compatibility for any module
that imports helpers directly from this path.
"""
from __future__ import annotations

from nowplaying.orchestrator.llm._reverse_lookup import ReverseLookupMixin
from nowplaying.orchestrator.llm._shazam_relevance import ShazamRelevanceMixin
from nowplaying.orchestrator.llm._track_change import TrackChangeMixin
from nowplaying.orchestrator.llm._track_guess import (
    TrackGuessMixin,
    _next_side_in_progression,  # noqa: F401 — re-export; tests import this name directly
)


class LLMHooksMixin(
    ReverseLookupMixin,
    ShazamRelevanceMixin,
    TrackChangeMixin,
    TrackGuessMixin,
):
    """LLM-assisted track-change and guess decisions.

    All state is accessed via ``self.state`` and ``self.llm`` —
    owned by ``Orchestrator.__init__``.
    No ``__init__`` defined here.
    """
