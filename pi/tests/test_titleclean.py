from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

import pytest
from nowplaying.titleclean import clean_title_regex


@pytest.mark.parametrize("raw,expected", [
    ("Penny Lane (2017 Mix)", "Penny Lane"),
    ("While My Guitar Gently Weeps (2018 Mix)", "While My Guitar Gently Weeps"),
    ("Now And Then (2023)", "Now And Then"),
    ("Strawberry Fields Forever (2015 Mix) - from (2017 Mix)", "Strawberry Fields Forever"),
    ("Lady Madonna (2015 Mix) from (2023 Mix)", "Lady Madonna"),
    ("Get Back (2015 Mix)", "Get Back"),
    ("Something (Half-Speed Master)", "Something"),
    ("A Hard Day's Night (Mono)", "A Hard Day's Night"),
    ("Hey Jude (Live)", "Hey Jude (Live)"),
    ("Blackbird (Acoustic)", "Blackbird (Acoustic)"),
    ("Revolution (Single Version)", "Revolution (Single Version)"),
    ("Sgt. Pepper's Lonely Hearts Club Band (Reprise)", "Sgt. Pepper's Lonely Hearts Club Band (Reprise)"),
    ("Hey Jude", "Hey Jude"),
    ("Bury Me", "Bury Me"),
    ("(2017 Mix)", "(2017 Mix)"),
    ("", ""),
])
def test_clean_title_regex(raw, expected):
    assert clean_title_regex(raw) == expected


import asyncio
from unittest import mock
from nowplaying.titleclean import clean_title


def _run(coro):
    return asyncio.run(coro)


def test_clean_title_uses_llm_when_enabled():
    from nowplaying.llm.title_clean import CleanTitle
    llm = mock.MagicMock()
    llm.enabled = True
    async def fake(raw):
        return CleanTitle(clean_title="Penny Lane")
    llm.clean_track_title = fake
    clean, source = _run(clean_title("Penny Lane (2017 Mix)", llm))
    assert clean == "Penny Lane"
    assert source == "llm"


def test_clean_title_falls_back_to_regex_when_disabled():
    llm = mock.MagicMock()
    llm.enabled = False
    clean, source = _run(clean_title("Penny Lane (2017 Mix)", llm))
    assert clean == "Penny Lane"
    assert source == "regex"


def test_clean_title_falls_back_when_llm_returns_heuristic():
    from nowplaying.llm import USE_HEURISTIC
    llm = mock.MagicMock()
    llm.enabled = True
    async def fake(raw):
        return USE_HEURISTIC
    llm.clean_track_title = fake
    clean, source = _run(clean_title("Get Back (2015 Mix)", llm))
    assert clean == "Get Back"
    assert source == "regex"


def test_clean_title_none_llm_uses_regex():
    clean, source = _run(clean_title("Get Back (2015 Mix)", None))
    assert clean == "Get Back"
    assert source == "regex"
