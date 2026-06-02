from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

import nowplaying.discovery.musicbrainz_lookup as mb_lookup
from nowplaying.discovery.musicbrainz_lookup import _pick_recording_length


# ---------------------------------------------------------------------------
# Pure-function tests (pre-existing)
# ---------------------------------------------------------------------------


def test_single_recording():
    assert _pick_recording_length([{"length": 254000, "score": 100}]) == 254

def test_multiple_agree():
    assert _pick_recording_length([
        {"length": 254000, "score": 100},
        {"length": 254000, "score": 100},
    ]) == 254

def test_disagree_same_score_picks_shortest():
    assert _pick_recording_length([
        {"length": 390506, "score": 100},
        {"length": 354040, "score": 100},
    ]) == 354

def test_mode_wins_over_shortest():
    assert _pick_recording_length([
        {"length": 390506, "score": 100},
        {"length": 354040, "score": 100},
        {"length": 354040, "score": 100},
    ]) == 354

def test_score_filter():
    assert _pick_recording_length([
        {"length": 300000, "score": 100},
        {"length": 200000, "score": 50},
    ]) == 300

def test_no_length_returns_none():
    assert _pick_recording_length([{"score": 100}]) is None

def test_empty_returns_none():
    assert _pick_recording_length([]) is None


# ---------------------------------------------------------------------------
# Async tests for recording_length_by_isrc
# ---------------------------------------------------------------------------


def _make_session_mock(status: int, json_data: dict | None = None) -> MagicMock:
    """Build a mock aiohttp.ClientSession context manager whose .get()
    returns a response with the given status and async .json()."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})

    # .get() is used as an async context manager: `async with session.get(...)`
    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=get_cm)

    # ClientSession itself is used as an async context manager
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return session_cm


def test_recording_length_by_isrc_happy_path():
    """MB returns a recording with length 163000 ms → function returns 163."""
    payload = {"recordings": [{"length": 163000, "score": 100}]}
    session_cm = _make_session_mock(200, payload)

    async def _run():
        with (
            patch.object(mb_lookup, "aiohttp") as mock_aiohttp,
            patch.object(mb_lookup, "asyncio") as mock_asyncio,
        ):
            # Wire ClientSession to return our mock
            mock_aiohttp.ClientSession.return_value = session_cm
            mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
            # Make the semaphore a real one so the async-with works; no-op sleep
            mock_asyncio.Semaphore = asyncio.Semaphore
            mock_asyncio.sleep = AsyncMock(return_value=None)
            # Reset module-level semaphore so _get_mb_rate_limit() creates fresh
            mb_lookup._mb_rate_limit = None
            result = await mb_lookup.recording_length_by_isrc("GBAYE0000001")
        return result

    assert asyncio.run(_run()) == 163


def test_recording_length_by_isrc_empty_isrc_no_http():
    """Empty ISRC → returns None immediately without making any HTTP call."""
    async def _run():
        with patch.object(mb_lookup, "aiohttp") as mock_aiohttp:
            result = await mb_lookup.recording_length_by_isrc("")
            mock_aiohttp.ClientSession.assert_not_called()
        return result

    assert asyncio.run(_run()) is None


def test_recording_length_by_isrc_non_200_returns_none():
    """Non-200 HTTP response → returns None."""
    session_cm = _make_session_mock(429, None)

    async def _run():
        with (
            patch.object(mb_lookup, "aiohttp") as mock_aiohttp,
            patch.object(mb_lookup, "asyncio") as mock_asyncio,
        ):
            mock_aiohttp.ClientSession.return_value = session_cm
            mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
            mock_asyncio.Semaphore = asyncio.Semaphore
            mock_asyncio.sleep = AsyncMock(return_value=None)
            mb_lookup._mb_rate_limit = None
            result = await mb_lookup.recording_length_by_isrc("GBAYE0000001")
        return result

    assert asyncio.run(_run()) is None


def test_recording_length_by_isrc_network_error_returns_none():
    """Network exception → returns None (exception is swallowed)."""
    import aiohttp as _aiohttp

    # Raise on __aenter__ of the session context manager
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(side_effect=_aiohttp.ClientConnectionError("boom"))
    session_cm.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        with (
            patch.object(mb_lookup, "aiohttp") as mock_aiohttp,
            patch.object(mb_lookup, "asyncio") as mock_asyncio,
        ):
            mock_aiohttp.ClientSession.return_value = session_cm
            mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
            mock_asyncio.Semaphore = asyncio.Semaphore
            mock_asyncio.sleep = AsyncMock(return_value=None)
            mb_lookup._mb_rate_limit = None
            result = await mb_lookup.recording_length_by_isrc("GBAYE0000001")
        return result

    assert asyncio.run(_run()) is None


def test_recording_length_by_isrc_empty_recordings_returns_none():
    """MB returns empty recordings list → returns None."""
    payload = {"recordings": []}
    session_cm = _make_session_mock(200, payload)

    async def _run():
        with (
            patch.object(mb_lookup, "aiohttp") as mock_aiohttp,
            patch.object(mb_lookup, "asyncio") as mock_asyncio,
        ):
            mock_aiohttp.ClientSession.return_value = session_cm
            mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())
            mock_asyncio.Semaphore = asyncio.Semaphore
            mock_asyncio.sleep = AsyncMock(return_value=None)
            mb_lookup._mb_rate_limit = None
            result = await mb_lookup.recording_length_by_isrc("GBAYE0000001")
        return result

    assert asyncio.run(_run()) is None
