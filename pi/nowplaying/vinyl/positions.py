"""Robust comparator for Discogs-style side-prefixed track positions.

Track positions on vinyl Discogs releases follow a `<side><index>` format:
'A1', 'A2', ... 'A10', 'B1', etc. String compare orders 'A10' BEFORE 'A2'
(lex says '1' < '2'), but disc order is 'A2' < 'A10'. This module parses
the side letter and the numeric component and compares them properly.

Non-side-prefixed positions (numeric disc-track like '1-1', or anything
without a single leading A-Z letter followed by digits) parse to None and
sort last in `position_cmp`.

See feature audible-event-wiring-regression.
"""

from __future__ import annotations

import re

# Side letter (single A-Z) + at least one digit somewhere in the
# remainder. Tolerates trailing letters/punctuation (e.g. 'A1a', 'A.1').
_POS_RE = re.compile(r"^([A-Z])\D*?(\d+)")


def parse_position(pos: str | None) -> tuple[str, int] | None:
    """Parse a Discogs position string into (side_letter, index).

    Returns None for None, empty strings, or any string that does not
    start with a single A-Z letter followed eventually by digits.

    Examples:
        parse_position('A1')   -> ('A', 1)
        parse_position('A10')  -> ('A', 10)
        parse_position('b3')   -> ('B', 3)   # lowercase tolerated
        parse_position('1-1')  -> None       # no side prefix
        parse_position('')     -> None
        parse_position(None)   -> None
    """
    if not pos:
        return None
    if not isinstance(pos, str):
        return None
    stripped = pos.strip().upper()
    if not stripped:
        return None
    m = _POS_RE.match(stripped)
    if not m:
        return None
    return (m.group(1), int(m.group(2)))


def position_cmp(a: str | None, b: str | None) -> int:
    """Disc-order comparator. Returns -1, 0, or 1.

    Order:
        1. Both parseable: sort by side letter, then by numeric index.
        2. Unparseable / None values sort LAST. Two unparseable values
           are considered equal.
    """
    pa = parse_position(a)
    pb = parse_position(b)
    if pa is None and pb is None:
        return 0
    if pa is None:
        return 1
    if pb is None:
        return -1
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def same_side(a: str | None, b: str | None) -> bool:
    """True iff both positions parse and share the same side letter."""
    pa = parse_position(a)
    pb = parse_position(b)
    if pa is None or pb is None:
        return False
    return pa[0] == pb[0]
