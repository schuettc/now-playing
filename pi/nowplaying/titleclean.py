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
from typing import Any

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


async def clean_title(raw: str, llm: Any | None) -> tuple[str, str]:
    """Return (clean_title, source). Prefer the LLM when available; fall
    back to the regex on disabled / USE_HEURISTIC / error. `source` is
    'llm' or 'regex'."""
    if llm is not None and getattr(llm, "enabled", False):
        from nowplaying.llm import USE_HEURISTIC
        try:
            verdict = await llm.clean_track_title(raw)
        except Exception:
            verdict = USE_HEURISTIC
        if verdict is not USE_HEURISTIC:
            clean = getattr(verdict, "clean_title", "") or ""
            clean = clean.strip()
            if clean:
                return clean, "llm"
    return clean_title_regex(raw), "regex"


async def clean_titles(raws: list[str], llm: Any | None) -> dict[str, tuple[str, str]]:  # skylos: ignore — consumed by population passes (Tasks 10-12)
    """Clean a batch of raw titles. Deduplicates identical inputs."""
    out: dict[str, tuple[str, str]] = {}
    for raw in raws:
        if raw in out:
            continue
        out[raw] = await clean_title(raw, llm)
    return out
