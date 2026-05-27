"""PredictionMixin — composed from four focused sub-modules.

Each sub-module owns one concern (under 500 LOC):
  _prediction_decay       — state-decay (_decay_pin_check, _check_state_decay)
  _prediction_advance     — advance/publish helpers (_try_advance_prediction,
                            _republish_current_prediction, …)
  _prediction_unmatched   — unmatched-heartbeat routing (_handle_unmatched_*,
                            _seed_prediction_from_last_vinyl, _publish_needs_id, …)
  _prediction_shazam_gate — Shazam gate, pin application, idle timer

Re-exports _build_predicted_payload so patch paths that reference
``nowplaying.orchestrator._prediction._build_predicted_payload``
(test_state_decay.py) continue to intercept the right binding.
"""
from __future__ import annotations

from nowplaying.orchestrator._prediction_advance import _AdvanceMixin
from nowplaying.orchestrator._prediction_decay import _DecayMixin
from nowplaying.orchestrator._prediction_shazam_gate import _ShazamGateMixin
from nowplaying.orchestrator._prediction_unmatched import _UnmatchedMixin
from nowplaying.orchestrator.prediction import _build_predicted_payload  # noqa: F401  # Why: re-exported so existing test patches of nowplaying.orchestrator._prediction._build_predicted_payload keep intercepting the binding used by _AdvanceMixin methods.


class PredictionMixin(
    _DecayMixin,
    _AdvanceMixin,
    _UnmatchedMixin,
    _ShazamGateMixin,
):
    """Unmatched heartbeat routing, prediction/advance, pin and idle logic.

    All state is accessed via ``self.state``, ``self.bcast``,
    ``self.fingerprint_enabled``, and ``self.llm`` —
    owned by ``Orchestrator.__init__``.
    No ``__init__`` defined here.
    """
