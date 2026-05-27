"""Album-name normalization for matching Shazam album strings against
MusicBrainz canonical titles.

Shazam returns edition-suffixed titles like
``"Brothers (Deluxe Remastered Anniversary Edition)"`` while MB persists
the canonical ``"Brothers"``. Naive lowercase equality misses; we strip
edition-marker parens/brackets when their content contains known keywords.

Edition keywords are matched case-insensitively. Non-edition parentheticals
(``"Mama Said Knock You Out (Live)"``) are left intact so we don't collide
distinct releases.
"""
from __future__ import annotations

import re

# Keywords that indicate an edition/reissue marker (case-insensitive).
# A parenthetical or bracketed suffix is only stripped if its inner content
# contains at least one of these tokens.
_EDITION_KEYWORDS = (
    "deluxe",
    "remaster",      # covers "remastered", "remasters"
    "remastered",
    "anniversary",
    "edition",
    "expanded",
    "bonus",
    "reissue",
    "special",
    "collector",     # collector's edition
    "extended",
    "version",       # "Album Version", "Single Version" (edition-ish)
)

_TRAILING_PAREN_RE = re.compile(r"\s*\(([^)]*)\)\s*$")
_TRAILING_BRACKET_RE = re.compile(r"\s*\[([^\]]*)\]\s*$")


def _has_edition_keyword(inner: str) -> bool:
    low = inner.lower()
    return any(kw in low for kw in _EDITION_KEYWORDS)


def normalize_album(name: str) -> str:
    """Return a normalized form of an album title for matching.

    - Lowercases + strips whitespace.
    - Repeatedly strips trailing ``(...)`` or ``[...]`` suffixes whose
      contents include an edition keyword (Deluxe, Remastered, etc).
    - Idempotent.
    """
    if not name:
        return ""
    s = name.strip()
    # Iteratively strip — handles "Foo (Remastered) (Bonus Tracks)".
    while True:
        m = _TRAILING_PAREN_RE.search(s)
        if m and _has_edition_keyword(m.group(1)):
            s = s[: m.start()].rstrip()
            continue
        m = _TRAILING_BRACKET_RE.search(s)
        if m and _has_edition_keyword(m.group(1)):
            s = s[: m.start()].rstrip()
            continue
        break
    return s.strip().lower()


def normalize_artist(name: str) -> str:
    """Lower + strip whitespace. No edition-suffix stripping — artists
    don't carry those, and stripping parens here would conflate
    distinct projects."""
    if not name:
        return ""
    return name.strip().lower()


__all__ = ["normalize_album", "normalize_artist"]
