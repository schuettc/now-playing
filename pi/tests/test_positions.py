"""Tests for nowplaying.vinyl.positions disc-order comparator.

See feature audible-event-wiring-regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the `nowplaying` package importable when pytest runs from the
# `pi/` directory or the repo root.
_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.vinyl.positions import (  # noqa: E402
    parse_position,
    position_cmp,
    same_side,
)


# ---- parse_position ---------------------------------------------------


def test_parse_position_simple():
    assert parse_position("A1") == ("A", 1)


def test_parse_position_two_digit():
    assert parse_position("A10") == ("A", 10)


def test_parse_position_side_b():
    assert parse_position("B2") == ("B", 2)


def test_parse_position_lowercase_normalized():
    assert parse_position("a3") == ("A", 3)


def test_parse_position_strips_whitespace():
    assert parse_position("  A4  ") == ("A", 4)


def test_parse_position_none():
    assert parse_position(None) is None


def test_parse_position_empty():
    assert parse_position("") is None


def test_parse_position_whitespace_only():
    assert parse_position("   ") is None


def test_parse_position_disc_track_style():
    # '1-1' has no side letter prefix.
    assert parse_position("1-1") is None


def test_parse_position_unparseable():
    assert parse_position("???") is None


def test_parse_position_letter_only():
    assert parse_position("A") is None


# ---- position_cmp -----------------------------------------------------


def test_cmp_a1_before_a2():
    assert position_cmp("A1", "A2") < 0


def test_cmp_a2_before_a10_disc_order():
    # Critical: string compare would put 'A10' < 'A2' (lex), which is the
    # exact bug this module exists to fix.
    assert position_cmp("A10", "A2") > 0
    assert position_cmp("A2", "A10") < 0


def test_cmp_a1_before_b1():
    assert position_cmp("A1", "B1") < 0


def test_cmp_a10_before_b1():
    # All of side A precedes all of side B.
    assert position_cmp("A10", "B1") < 0


def test_cmp_equal():
    assert position_cmp("A2", "A2") == 0


def test_cmp_none_sorts_last():
    assert position_cmp(None, "A1") > 0
    assert position_cmp("A1", None) < 0


def test_cmp_both_none_equal():
    assert position_cmp(None, None) == 0


def test_cmp_unparseable_sorts_last():
    assert position_cmp("???", "A1") > 0
    assert position_cmp("A1", "???") < 0


def test_cmp_both_unparseable_equal():
    assert position_cmp("???", "!!!") == 0


# ---- same_side -------------------------------------------------------


def test_same_side_true():
    assert same_side("A1", "A2") is True


def test_same_side_a10_a2_true():
    assert same_side("A10", "A2") is True


def test_same_side_false():
    assert same_side("A1", "B1") is False


def test_same_side_none():
    assert same_side(None, "A1") is False
    assert same_side("A1", None) is False
    assert same_side(None, None) is False


def test_same_side_unparseable():
    assert same_side("???", "A1") is False
