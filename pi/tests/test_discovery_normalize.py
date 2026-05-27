"""Tests for nowplaying.discovery._normalize."""
from __future__ import annotations

import pytest

from nowplaying.discovery._normalize import normalize_album, normalize_artist


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The headliner: Shazam-returned Black Keys Brothers edition string.
        ("Brothers (Deluxe Remastered Anniversary Edition)", "brothers"),
        # Common edition-marker variants.
        ("Abbey Road (Remastered)", "abbey road"),
        ("Rumours (Expanded Edition)", "rumours"),
        ("OK Computer (Bonus Tracks)", "ok computer"),
        ("Songs in the Key of Life (2014 Reissue)", "songs in the key of life"),
        ("Foo (Remastered) (Bonus Tracks)", "foo"),
        ("Whatever [Remastered]", "whatever"),
        # Negative cases — non-edition parentheticals must stay intact.
        ("Mama Said Knock You Out (Live)", "mama said knock you out (live)"),
        ("Wish You Were Here", "wish you were here"),
        ("E.T. (I Want to Come Home)", "e.t. (i want to come home)"),
        # Whitespace / casing.
        ("  Brothers  ", "brothers"),
        ("BROTHERS", "brothers"),
        ("", ""),
    ],
)
def test_normalize_album(raw: str, expected: str) -> None:
    assert normalize_album(raw) == expected


def test_normalize_album_idempotent() -> None:
    samples = [
        "Brothers (Deluxe Remastered Anniversary Edition)",
        "Abbey Road (Remastered)",
        "Mama Said Knock You Out (Live)",
        "Wish You Were Here",
    ]
    for s in samples:
        once = normalize_album(s)
        twice = normalize_album(once)
        assert once == twice, f"not idempotent for {s!r}: {once!r} -> {twice!r}"


def test_normalize_artist_simple() -> None:
    assert normalize_artist("The Black Keys") == "the black keys"
    assert normalize_artist("  Radiohead  ") == "radiohead"
    assert normalize_artist("") == ""
