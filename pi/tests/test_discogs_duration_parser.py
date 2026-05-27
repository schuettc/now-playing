"""Tests for the Discogs duration parser + leaf-tracklist iterator.

The parser used to drop ~50% of catalog track durations because
`discogs_sync.fetch_detail` walked only the top-level tracklist and never
descended into the `sub_tracks` of multi-disc / index/heading rows. These
tests pin down the round-trip semantics so future edits don't regress.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "pi" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def parser_module():
    # Importing discogs_sync requires discogs_client + dotenv, both stdlib-free.
    # The test environment (`pi/.venv`) has them; if not, skip cleanly.
    try:
        import discogs_sync  # type: ignore — discogs_sync is a scripts/ file, not a package; mypy has no stub for it and the conditional skip means it's safe to ignore the missing-import error
    except ModuleNotFoundError as e:
        pytest.skip(f"discogs_sync import failed in this environment: {e}")
    return discogs_sync


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1:21", 81),
        ("3:45", 225),
        ("15:30", 930),
        ("0:00", 0),
        ("1:02:03", 3723),
        ("", None),
        ("   ", None),
        (None, None),
        ("abc", None),
        ("3:", None),       # int("") raises ValueError
        ("-1:30", None),    # negative components rejected
    ],
)
def test_parse_discogs_duration(parser_module, raw, expected):
    assert parser_module._parse_discogs_duration(raw) == expected


def test_backwards_compat_alias(parser_module):
    # External imports (if any) of the legacy name should keep working.
    assert parser_module.parse_duration("2:00") == 120
    assert parser_module.parse_duration is parser_module._parse_discogs_duration


def test_iter_leaf_tracks_flat(parser_module):
    tracklist = [
        {"position": "A1", "title": "Foo", "duration": "3:00"},
        {"position": "A2", "title": "Bar", "duration": "4:15"},
        {"position": "", "title": "(silence)", "duration": ""},  # skip
    ]
    out = list(parser_module.iter_leaf_tracks(tracklist))
    assert out == [
        ("A1", "Foo", "3:00"),
        ("A2", "Bar", "4:15"),
    ]


def test_iter_leaf_tracks_descends_sub_tracks(parser_module):
    """Beatles Anthology / OK Computer OKNOTOK shape: heading row with
    empty position + duration, real tracks under `sub_tracks`."""
    tracklist = [
        {
            "position": "",
            "title": "Disc 1",
            "duration": "",
            "sub_tracks": [
                {"position": "1-1", "title": "Free As A Bird", "duration": "4:25"},
                {"position": "1-2", "title": "Glass Onion", "duration": "2:08"},
            ],
        },
        {"position": "2-1", "title": "Plain Track", "duration": "3:00"},
    ]
    out = list(parser_module.iter_leaf_tracks(tracklist))
    assert out == [
        ("1-1", "Free As A Bird", "4:25"),
        ("1-2", "Glass Onion", "2:08"),
        ("2-1", "Plain Track", "3:00"),
    ]


def test_iter_leaf_tracks_handles_none_input(parser_module):
    assert list(parser_module.iter_leaf_tracks(None)) == []
    assert list(parser_module.iter_leaf_tracks([])) == []
