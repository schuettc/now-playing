"""Tests for POST /api/dismiss-guess and the _guess_is_dismissed
suppression helper.

Covers the contract specified in
docs/features/identify-guess-confirm/plan.md:
  - happy path records the entry; 200 with echo body
  - 4xx machine-readable `reason` codes (bad-request)
  - 503 when state missing
  - TTL eviction on read
  - pending_guess cleared when it matches the dismissal
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying import control  # noqa: E402
from nowplaying.main import (  # noqa: E402
    DISMISSED_GUESS_TTL_S,
    State,
    _guess_is_dismissed,
)


def _mk_request(state, body):
    app = {"state": state}
    req = MagicMock(spec=web.Request)
    req.app = app
    req.json = AsyncMock(return_value=body)
    return req


def _run(coro):
    return asyncio.run(coro)


def _decode(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


# ---- _guess_is_dismissed helper -------------------------------------


def test_helper_returns_false_for_missing_entry():
    assert _guess_is_dismissed({}, 100, "A1", 0.0) is False


def test_helper_returns_true_within_ttl():
    d = {(100, "A1"): 1000.0}
    assert _guess_is_dismissed(d, 100, "A1", 1000.0 + 100.0) is True


def test_helper_evicts_and_returns_false_past_ttl():
    d = {(100, "A1"): 1000.0}
    assert (
        _guess_is_dismissed(d, 100, "A1", 1000.0 + DISMISSED_GUESS_TTL_S + 1)
        is False
    )
    assert (100, "A1") not in d, "stale entries should evict on read"


def test_helper_different_release_or_position_does_not_match():
    d = {(100, "A1"): 1000.0}
    assert _guess_is_dismissed(d, 200, "A1", 1000.0) is False
    assert _guess_is_dismissed(d, 100, "A2", 1000.0) is False


# ---- POST /api/dismiss-guess ----------------------------------------


def test_dismiss_guess_happy_path_records_entry():
    state = State()
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    resp = _run(control.dismiss_guess(req))
    assert resp.status == 200
    body = _decode(resp)
    assert body["ok"] is True
    assert body["release_id"] == 100
    assert body["track_position"] == "B3"
    assert (100, "B3") in state.dismissed_guesses


def test_dismiss_guess_clears_matching_pending_guess():
    state = State()
    state.last_vinyl = {"release_id": 100}
    state.pending_guess = {"position": "B3", "title": "X", "confidence": "high", "source": "llm"}
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    _run(control.dismiss_guess(req))
    assert state.pending_guess is None


def test_dismiss_guess_does_not_clear_pending_for_other_track():
    state = State()
    state.last_vinyl = {"release_id": 100}
    state.pending_guess = {"position": "A1", "title": "Y", "confidence": "high", "source": "llm"}
    req = _mk_request(state, {"release_id": 100, "track_position": "B3"})
    _run(control.dismiss_guess(req))
    # Different position; pending_guess preserved.
    assert state.pending_guess is not None
    assert state.pending_guess["position"] == "A1"


def test_dismiss_guess_bad_request_missing_field():
    state = State()
    req = _mk_request(state, {"release_id": 100})
    resp = _run(control.dismiss_guess(req))
    assert resp.status == 400
    body = _decode(resp)
    assert body["ok"] is False
    assert body["reason"] == "bad-request"


def test_dismiss_guess_bad_request_wrong_type():
    state = State()
    req = _mk_request(state, {"release_id": "not-an-int", "track_position": "A1"})
    resp = _run(control.dismiss_guess(req))
    assert resp.status == 400
    assert _decode(resp)["reason"] == "bad-request"


def test_dismiss_guess_503_when_state_missing():
    req = MagicMock(spec=web.Request)
    req.app = {"state": None}
    resp = _run(control.dismiss_guess(req))
    assert resp.status == 503


# ---- source-flip + idle clear ---------------------------------------


def test_dismissed_guesses_cleared_on_state_construct():
    state = State()
    assert state.dismissed_guesses == {}
