"""Track-title cleaning hook.

Verdict dataclass, Anthropic tool spec, and prompt builder for
`LLMAssist.clean_track_title`. The method itself lives in `_assist.py`.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class CleanTitle:
    """Output of `clean_track_title`: the canonical title for scrobbling."""

    clean_title: str


_CLEAN_TITLE_TOOL_SPEC: dict = {
    "name": "clean_track_title",
    "description": (
        "Return the canonical song title for a catalog track, removing "
        "remaster/mix/year/edition annotations but preserving annotations "
        "that mark a genuinely distinct recording."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "clean_title": {
                "type": "string",
                "description": (
                    "The canonical title. Remove remaster/mix/year/master/"
                    "edition tags such as '(2017 Mix)', '(Remastered)', "
                    "'(Half-Speed Master)', '(Mono)', '(2023)', and trailing "
                    "'- from (...)' / 'from (...)' fragments. KEEP tags that "
                    "denote a distinct recording: (Live), (Acoustic), (Demo), "
                    "(Reprise), (Session), (Single Version). Never invent or "
                    "translate text. If nothing should be removed, return the "
                    "title unchanged."
                ),
            },
        },
        "required": ["clean_title"],
    },
}


def _build_clean_title_prompt(raw_title: str) -> str:
    """Render the prompt for `clean_track_title`."""
    return (
        "You normalize vinyl track titles for scrobbling.\n"
        "Given a catalog track title, return the canonical song title.\n"
        "Remove remaster/mix/year/master/edition annotations — e.g. "
        "'(2017 Mix)', '(Remastered)', '(Half-Speed Master)', '(Mono)', "
        "'(2023)', and trailing '- from (...)' or 'from (...)' fragments.\n"
        "KEEP annotations that mark a distinct recording: (Live), (Acoustic), "
        "(Demo), (Reprise), (Session), (Single Version).\n"
        "Never invent or translate text. If nothing should be removed, return "
        "the title unchanged.\n\n"
        f"Title: {raw_title}\n"
    )
