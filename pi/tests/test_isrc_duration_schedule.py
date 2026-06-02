"""Tests for fire-and-forget ISRC→duration enrichment in recognize_proto."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_PI_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PI_ROOT.parent / "pi" / "scripts"
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "pi") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "pi"))

import scripts.recognize_proto as rp  # noqa: E402


# ---------------------------------------------------------------------------
# _run_isrc_duration behavioural tests
# ---------------------------------------------------------------------------


def test_run_isrc_duration_discogs_path():
    """MB returns 163s and release_id is set: discogs setter is called."""
    rp._isrc_no_duration.discard("GBAYE0000001")

    mock_setter = MagicMock(return_value=1)

    async def _run():
        with (
            patch.object(
                rp.musicbrainz_lookup,
                "recording_length_by_isrc",
                new=AsyncMock(return_value=163),
            ),
            patch.object(rp, "_set_discogs_duration", mock_setter),
        ):
            await rp._run_isrc_duration(
                "GBAYE0000001",
                release_id=42,
                mbid=None,
                position="A1",
            )

    asyncio.run(_run())
    mock_setter.assert_called_once_with(42, "A1", 163)


def test_run_isrc_duration_negative_cache_on_none():
    """MB returns None: ISRC is added to negative-cache set; no setter called."""
    isrc = "NEGATIVE0001"
    rp._isrc_no_duration.discard(isrc)

    mock_setter = MagicMock()

    async def _run():
        with (
            patch.object(
                rp.musicbrainz_lookup,
                "recording_length_by_isrc",
                new=AsyncMock(return_value=None),
            ),
            patch.object(rp, "_set_discogs_duration", mock_setter),
            patch.object(rp, "_set_discovered_duration", mock_setter),
        ):
            await rp._run_isrc_duration(
                isrc,
                release_id=42,
                mbid=None,
                position="A1",
            )

    asyncio.run(_run())
    assert isrc in rp._isrc_no_duration
    mock_setter.assert_not_called()


def test_run_isrc_duration_discovered_path():
    """MB returns 200s and only mbid is set: discovered setter is called."""
    rp._isrc_no_duration.discard("DISCOVERED001")

    mock_setter = MagicMock(return_value=1)

    async def _run():
        with (
            patch.object(
                rp.musicbrainz_lookup,
                "recording_length_by_isrc",
                new=AsyncMock(return_value=200),
            ),
            patch.object(rp, "_set_discovered_duration", mock_setter),
        ):
            await rp._run_isrc_duration(
                "DISCOVERED001",
                release_id=None,
                mbid="abc-mbid-123",
                position="B2",
            )

    asyncio.run(_run())
    mock_setter.assert_called_once_with("abc-mbid-123", "B2", 200)


# ---------------------------------------------------------------------------
# _schedule_isrc_duration gating tests
# ---------------------------------------------------------------------------


def _make_loop_mock():
    """Return a mock loop whose create_task records the coroutine."""
    loop = MagicMock()
    created = []

    def capture_task(coro):
        created.append(coro)
        task = MagicMock()
        task.done.return_value = False
        return task

    loop.create_task.side_effect = capture_task
    return loop, created


def _close_coroutines(created):
    for c in created:
        try:
            c.close()
        except Exception:
            pass


def test_schedule_no_task_when_duration_already_set():
    """Matched track already has duration_seconds: no task created."""
    result = {
        "isrc": "GBAYE0000001",
        "track_position": "A1",
        "release_id": 42,
        "tracklist": [
            {"position": "A1", "duration_seconds": 180},
        ],
    }
    loop, created = _make_loop_mock()
    with patch("asyncio.get_running_loop", return_value=loop):
        rp._schedule_isrc_duration(result)
    _close_coroutines(created)
    loop.create_task.assert_not_called()


def test_schedule_no_task_when_isrc_absent():
    """No isrc in result: no task created."""
    result = {
        "track_position": "A1",
        "release_id": 42,
        "tracklist": [{"position": "A1", "duration_seconds": None}],
    }
    loop, created = _make_loop_mock()
    with patch("asyncio.get_running_loop", return_value=loop):
        rp._schedule_isrc_duration(result)
    _close_coroutines(created)
    loop.create_task.assert_not_called()


def test_schedule_no_task_when_no_write_key():
    """Neither release_id nor release_mbid: no task created."""
    result = {
        "isrc": "GBAYE0000002",
        "track_position": "A1",
        "tracklist": [{"position": "A1", "duration_seconds": None}],
    }
    loop, created = _make_loop_mock()
    with patch("asyncio.get_running_loop", return_value=loop):
        rp._schedule_isrc_duration(result)
    _close_coroutines(created)
    loop.create_task.assert_not_called()


def test_schedule_creates_task_when_all_conditions_met():
    """All conditions satisfied: loop.create_task is called once."""
    isrc = "SCHEDULE0001"
    rp._isrc_no_duration.discard(isrc)
    # Clear any stale in-flight entry
    rp._in_flight_isrc_dur.pop((42, "A1"), None)

    result = {
        "isrc": isrc,
        "track_position": "A1",
        "release_id": 42,
        "tracklist": [{"position": "A1", "duration_seconds": None}],
    }
    loop, created = _make_loop_mock()
    with patch("asyncio.get_running_loop", return_value=loop):
        rp._schedule_isrc_duration(result)
    _close_coroutines(created)
    loop.create_task.assert_called_once()
