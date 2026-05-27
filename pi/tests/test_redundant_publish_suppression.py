"""Tests for the redundant-publish suppression gate in Broadcaster.

The Sonos UPnP subscription auto-renews every ~10 minutes. Each renewal
triggers a NOTIFY that re-asserts the current Sonos state. For vinyl
Line-In with no audio flowing the payload is always
  {state: "PLAYING", source: "vinyl", title: None, ...}
If this identical payload is broadcast to kiosk clients, they leave the
idle-clock screen and return to VinylIdentifying — a visible UX regression.

The fix: ``Broadcaster.publish`` compares content-significant fields of
the new payload against the previous publish and suppresses the call when
they are identical.

Covered scenarios
-----------------
1. First publish always fires (no prior state).
2. Identical payload is suppressed (send_json NOT called the second time).
3. Payload with different match_method is NOT suppressed.
4. Null-title vinyl repeated (Sonos resubscribe) → second suppressed.
5. Null-title vinyl → STOPPED → NOT suppressed (state changed).
6. Timestamp-only difference → still suppressed (ts excluded from check).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.api.broadcaster import (  # noqa: E402
    Broadcaster,
    _is_stopped_to_empty_vinyl,
    _payloads_are_redundant,
)


# ---------------------------------------------------------------------------
# Pure-function tests for _payloads_are_redundant
# ---------------------------------------------------------------------------


def test_redundant_returns_false_when_prev_is_none() -> None:
    """First publish has no prior state — must never be suppressed."""
    curr = {"state": "PLAYING", "source": "vinyl", "title": None}
    redundant, _ = _payloads_are_redundant(None, curr)
    assert redundant is False


def test_redundant_identical_payloads() -> None:
    payload = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "track_position": None,
        "match_method": "sonos-didl",
        "art_url": None,
        "album": None,
        "guess": None,
        "predicted": None,
        "ts": "2026-05-19T10:22:27Z",
    }
    # Content fields are identical even with same ts — should be redundant
    redundant, reason = _payloads_are_redundant(payload, dict(payload))
    assert redundant is True
    assert reason == "content-identical"


def test_redundant_ts_difference_does_not_prevent_suppression() -> None:
    """Timestamp changes on every Sonos NOTIFY — must be ignored."""
    prev = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "ts": "2026-05-19T10:22:27Z",
    }
    curr = dict(prev)
    curr["ts"] = "2026-05-19T10:32:26Z"  # 10 minutes later — resubscribe
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is True


def test_redundant_match_method_change_not_redundant() -> None:
    """match_method is a content field — blind→fingerprint must NOT suppress."""
    base = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": "Pitiful",
        "artist": "Mavis Staples",
        "release_id": 12345,
        "track_position": "A1",
        "match_method": "blind",
        "art_url": None,
        "album": "Mavis Staples",
        "guess": None,
        "predicted": None,
    }
    updated = dict(base)
    updated["match_method"] = "fingerprint-anchor"
    redundant, _ = _payloads_are_redundant(base, updated)
    assert redundant is False


def test_redundant_state_change_not_redundant() -> None:
    """State change (PLAYING→STOPPED) must NOT be suppressed."""
    prev = {"state": "PLAYING", "source": "vinyl", "title": None}
    curr = {"state": "STOPPED", "source": "vinyl", "title": None}
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is False


def test_redundant_title_change_not_redundant() -> None:
    """A real track starting (title appears) must NOT be suppressed."""
    prev = {"state": "PLAYING", "source": "vinyl", "title": None}
    curr = {"state": "PLAYING", "source": "vinyl", "title": "Sympathy For The Devil"}
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is False


# ---------------------------------------------------------------------------
# Integration tests for Broadcaster.publish
# ---------------------------------------------------------------------------


def _make_ws() -> MagicMock:
    """Fake connected WebSocketResponse."""
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture()
def bcast() -> Broadcaster:
    return Broadcaster()


@pytest.mark.asyncio
async def test_first_publish_always_fires(bcast: Broadcaster) -> None:
    """First publish has no prior state — always sends to clients."""
    ws = _make_ws()
    await bcast.add(ws)
    payload = {"state": "PLAYING", "source": "vinyl", "title": None, "ts": "t1"}
    await bcast.publish(payload)
    ws.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_payload_suppressed(bcast: Broadcaster) -> None:
    """Second publish with identical content fields is suppressed."""
    ws = _make_ws()
    await bcast.add(ws)
    payload = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "track_position": None,
        "match_method": "sonos-didl",
        "art_url": None,
        "album": None,
        "guess": None,
        "predicted": None,
        "ts": "2026-05-19T10:22:27Z",
    }
    await bcast.publish(payload)
    # Second publish — different ts, identical content
    payload2 = dict(payload)
    payload2["ts"] = "2026-05-19T10:32:26Z"
    await bcast.publish(payload2)

    # send_json called exactly once (first publish only)
    assert ws.send_json.await_count == 1


@pytest.mark.asyncio
async def test_match_method_change_not_suppressed(bcast: Broadcaster) -> None:
    """Pitiful (blind) → Pitiful (fingerprint-anchor): NOT suppressed."""
    ws = _make_ws()
    await bcast.add(ws)
    base = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": "Pitiful",
        "artist": "Mavis Staples",
        "release_id": 12345,
        "track_position": "A1",
        "match_method": "blind",
        "art_url": None,
        "album": "Mavis Staples",
        "guess": None,
        "predicted": None,
        "ts": "t1",
    }
    await bcast.publish(base)
    updated = dict(base)
    updated["match_method"] = "fingerprint-anchor"
    updated["ts"] = "t2"
    await bcast.publish(updated)

    assert ws.send_json.await_count == 2


@pytest.mark.asyncio
async def test_null_title_vinyl_resubscribe_suppressed(bcast: Broadcaster) -> None:
    """Null-title vinyl repeated (Sonos resubscribe scenario) — second suppressed."""
    ws = _make_ws()
    await bcast.add(ws)
    payload = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "track_position": None,
        "match_method": "sonos-didl",
        "art_url": None,
        "album": None,
        "guess": None,
        "predicted": None,
        "ts": "2026-05-19T10:22:27Z",
    }
    await bcast.publish(payload)
    resubscribed = dict(payload)
    resubscribed["ts"] = "2026-05-19T10:32:26Z"
    await bcast.publish(resubscribed)

    assert ws.send_json.await_count == 1


@pytest.mark.asyncio
async def test_null_title_then_stopped_not_suppressed(bcast: Broadcaster) -> None:
    """Null-title vinyl → STOPPED: NOT suppressed (state changed)."""
    ws = _make_ws()
    await bcast.add(ws)
    playing = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "track_position": None,
        "match_method": "sonos-didl",
        "art_url": None,
        "album": None,
        "guess": None,
        "predicted": None,
        "ts": "t1",
    }
    stopped = dict(playing)
    stopped["state"] = "STOPPED"
    stopped["ts"] = "t2"

    await bcast.publish(playing)
    await bcast.publish(stopped)

    assert ws.send_json.await_count == 2


@pytest.mark.asyncio
async def test_suppression_log_line_emitted(bcast: Broadcaster) -> None:
    """When suppression fires, the log line includes 'redundant (skipped)' and reason."""
    ws = _make_ws()
    await bcast.add(ws)
    payload = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "ts": "t1",
    }
    await bcast.publish(payload)
    dup = dict(payload)
    dup["ts"] = "t2"

    with patch("nowplaying.api.broadcaster.log") as mock_log:
        await bcast.publish(dup)
        # Exactly one info call, and it contains the suppression message
        assert mock_log.info.call_count == 1
        call_args = mock_log.info.call_args[0]
        assert "redundant (skipped)" in call_args[0]
        assert "reason=" in call_args[0]


# ---------------------------------------------------------------------------
# New tests: _is_stopped_to_empty_vinyl helper
# ---------------------------------------------------------------------------


def test_stopped_to_empty_vinyl_suppressed() -> None:
    """STOPPED → bare vinyl-PLAYING with no metadata → suppressed (new case)."""
    prev = {"state": "STOPPED", "source": "vinyl", "title": None}
    curr = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "match_method": None,
    }
    assert _is_stopped_to_empty_vinyl(prev, curr) is True
    redundant, reason = _payloads_are_redundant(prev, curr)
    assert redundant is True
    assert reason == "stopped-to-empty-vinyl"


def test_stopped_to_vinyl_with_title_not_suppressed() -> None:
    """STOPPED → vinyl-PLAYING with title → NOT suppressed (real track started)."""
    prev = {"state": "STOPPED", "source": "vinyl", "title": None}
    curr = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": "Pitiful",
        "artist": "Mavis Staples",
        "release_id": 12345,
        "match_method": "shazam",
    }
    assert _is_stopped_to_empty_vinyl(prev, curr) is False
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is False


def test_stopped_to_airplay_with_metadata_not_suppressed() -> None:
    """STOPPED → airplay-PLAYING with metadata → NOT suppressed (source change)."""
    prev = {"state": "STOPPED", "source": "vinyl", "title": None}
    curr = {
        "state": "PLAYING",
        "source": "airplay",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "release_id": 67890,
        "match_method": "sonos-didl",
    }
    assert _is_stopped_to_empty_vinyl(prev, curr) is False
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is False


def test_stopped_to_vinyl_with_fingerprint_not_suppressed() -> None:
    """STOPPED → vinyl-PLAYING with match_method=fingerprint → NOT suppressed."""
    prev = {"state": "STOPPED", "source": "vinyl", "title": None}
    curr = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,  # title may still be absent mid-recognition
        "artist": None,
        "release_id": None,
        "match_method": "fingerprint",
    }
    assert _is_stopped_to_empty_vinyl(prev, curr) is False
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is False


def test_playing_to_stopped_not_suppressed() -> None:
    """PLAYING → STOPPED → NOT suppressed (idle-entry direction)."""
    prev = {"state": "PLAYING", "source": "vinyl", "title": None}
    curr = {
        "state": "STOPPED",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "match_method": None,
    }
    assert _is_stopped_to_empty_vinyl(prev, curr) is False
    redundant, _ = _payloads_are_redundant(prev, curr)
    assert redundant is False


def test_consecutive_identical_playing_vinyl_empty_still_suppressed() -> None:
    """Two consecutive identical PLAYING-vinyl-empty → still suppressed (PR #185 unchanged)."""
    payload = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "track_position": None,
        "match_method": "sonos-didl",
        "art_url": None,
        "album": None,
        "guess": None,
        "predicted": None,
        "ts": "2026-05-19T10:22:27Z",
    }
    redundant, reason = _payloads_are_redundant(payload, dict(payload))
    assert redundant is True
    assert reason == "content-identical"


def test_stopped_to_stopped_suppressed() -> None:
    """STOPPED → STOPPED → suppressed (existing content-identical case unchanged)."""
    payload = {
        "state": "STOPPED",
        "source": "vinyl",
        "title": None,
        "artist": None,
        "release_id": None,
        "track_position": None,
        "match_method": None,
        "art_url": None,
        "album": None,
        "guess": None,
        "predicted": None,
    }
    redundant, reason = _payloads_are_redundant(payload, dict(payload))
    assert redundant is True
    assert reason == "content-identical"


# ---------------------------------------------------------------------------
# Dict-aliasing regression test
# ---------------------------------------------------------------------------
# When the orchestrator publishes state.last_vinyl and pin_track later mutates
# state.last_vinyl in place before re-publishing, the broadcaster used to store
# the same dict reference as bcast._last. The second publish then compared the
# mutated dict against itself and incorrectly flagged it content-identical,
# silently dropping the user-pin update. The fix snapshots the payload on
# store so caller mutations don't reach _last.
#
# See docs/features/broadcaster-suppresses-pin-publish/.


@pytest.mark.asyncio
async def test_mutated_same_dict_not_suppressed(bcast: Broadcaster) -> None:
    """Publishing the same dict reference twice with in-place mutations
    between calls must NOT be suppressed as content-identical."""
    ws = _make_ws()
    await bcast.add(ws)
    payload = {
        "state": "PLAYING",
        "source": "vinyl",
        "title": "Dirty Blue Balloons",
        "artist": "Failure",
        "release_id": 31427573,
        "track_position": "B8",
        "match_method": "predicted",
        "art_url": "/art/31427573",
        "album": "Fantastic Planet",
        "guess": None,
        "predicted": True,
        "ts": "t1",
    }
    await bcast.publish(payload)
    # Caller mutates the SAME dict in place (this is what pin_track does
    # when it calls _apply_pin_to_locked on state.last_vinyl).
    payload["title"] = "Solaris"
    payload["track_position"] = "B9"
    payload["match_method"] = "user-identified"
    payload["predicted"] = False
    payload["ts"] = "t2"
    await bcast.publish(payload)

    # Both publishes must have reached the client.
    assert ws.send_json.await_count == 2
    # The second message must carry the new title — defends against a
    # naive fix that copies payload but still serves stale state.
    second_payload = ws.send_json.await_args_list[1].args[0]["payload"]
    assert second_payload["title"] == "Solaris"
    assert second_payload["track_position"] == "B9"
