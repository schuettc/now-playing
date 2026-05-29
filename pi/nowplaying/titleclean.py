"""Canonical track-title cleaning.

Discogs ships track titles with remaster/mix annotations baked in
(e.g. "Penny Lane (2017 Mix)"). For Last.fm aggregation and MusicBrainz
duration-matching we want the canonical title ("Penny Lane"), while
keeping annotations that mark a genuinely distinct recording ("(Live)").

`clean_title_regex` is the always-available conservative fallback;
`clean_title` (added later) prefers the LLM and falls back to regex.
"""
from __future__ import annotations

import re

# Annotations that mark a distinct recording — never strip these.
_KEEP = re.compile(r"\b(live|acoustic|demo|reprise|session|single version)\b", re.I)
# Mix/master/year/edition markers — strip groups that contain one of these.
_STRIP = re.compile(r"\b(mix|remaster|remastered|master|mono|stereo|edition|\d{4})\b", re.I)
# A trailing "(...)" or "[...]" group, optionally preceded by "- " and/or "from ".
_TRAILING = re.compile(r"\s*(?:-\s*)?(?:from\s+)?[\(\[]([^\(\)\[\]]*)[\)\]]\s*$", re.I)


def clean_title_regex(raw: str) -> str:
    """Strip trailing mix/year/remaster annotations; keep performance
    variants. Conservative: only strips a trailing parenthetical when its
    inner text matches a strip-keyword AND no keep-keyword. Repeats to
    handle stacked groups like "X (2015 Mix) - from (2017 Mix)". Returns
    the original string if stripping would empty it."""
    if not raw:
        return raw
    out = raw.strip()
    while True:
        m = _TRAILING.search(out)
        if not m:
            break
        inner = m.group(1)
        if _STRIP.search(inner) and not _KEEP.search(inner):
            out = out[: m.start()].rstrip(" -")
            continue
        break
    return out.strip() or raw
