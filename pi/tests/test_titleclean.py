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


from nowplaying.titleclean import clean_title


def test_clean_title_returns_regex_result():
    assert clean_title("Penny Lane (2017 Mix)") == ("Penny Lane", "regex")


def test_clean_title_source_is_always_regex():
    _, source = clean_title("Get Back (2015 Mix)")
    assert source == "regex"


def test_clean_title_passthrough_unchanged():
    assert clean_title("Bury Me") == ("Bury Me", "regex")


def test_clean_title_keeps_performance_variant():
    assert clean_title("Hey Jude (Live)") == ("Hey Jude (Live)", "regex")
