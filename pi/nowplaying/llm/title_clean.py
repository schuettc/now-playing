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
                    "The canonical title. Mix/year/remaster/master/edition "
                    "annotations MUST ALWAYS be removed — they are NEVER part "
                    "of the canonical song title. Remove tags such as "
                    "'(2017 Mix)', '(2015 Mix)', '(2023 Mix)', '(2025 Mix)', "
                    "'(Remastered)', '(2009 Remaster)', '(Half-Speed Master)', "
                    "'(Mono)', '(Stereo)', '(2023)', '(Deluxe Edition)', and "
                    "trailing '- from (...)' / 'from (...)' fragments. KEEP "
                    "tags that denote a genuinely distinct recording: (Live), "
                    "(Acoustic), (Demo), (Reprise), (Session), "
                    "(Single Version). Never invent or translate text. If "
                    "nothing should be removed, return the title unchanged."
                ),
            },
        },
        "required": ["clean_title"],
    },
}


def _build_clean_title_prompt(raw_title: str) -> str:
    """Render the prompt for `clean_track_title`. Few-shot + firm
    instruction: Haiku under a zero-shot prompt left ~half of the
    "(YYYY Mix)" annotations in place, so we show worked examples of the
    exact failure cases and state the rule imperatively."""
    return (
        "You normalize vinyl track titles for Last.fm scrobbling. Return the "
        "canonical song title with remaster/mix/year/edition annotations "
        "REMOVED.\n\n"
        "ALWAYS remove trailing parenthetical annotations indicating a "
        "remaster, mix, master, channel, or year — e.g. '(2017 Mix)', "
        "'(2015 Mix)', '(2023 Mix)', '(Remastered)', '(2009 Remaster)', "
        "'(Half-Speed Master)', '(Mono)', '(Stereo)', '(2023)', "
        "'(Deluxe Edition)' — and trailing connective fragments like "
        "'- from (2017 Mix)' or 'from (2023 Mix)'. These come from remaster "
        "compilations and are NEVER part of the canonical song title.\n\n"
        "KEEP annotations that denote a genuinely different recording or "
        "performance: '(Live)', '(Acoustic)', '(Demo)', '(Reprise)', "
        "'(Session)', '(Single Version)'.\n\n"
        "Never invent, translate, or rephrase. Only remove annotations. If a "
        "title has no such annotation, return it unchanged.\n\n"
        "Examples:\n"
        "  'Penny Lane (2017 Mix)' -> 'Penny Lane'\n"
        "  'Strawberry Fields Forever (2015 Mix) - from (2017 Mix)' -> "
        "'Strawberry Fields Forever'\n"
        "  'Lady Madonna (2015 Mix) from (2023 Mix)' -> 'Lady Madonna'\n"
        "  'I Am The Walrus (2023 Mix)' -> 'I Am The Walrus'\n"
        "  'Revolution (2025 Mix)' -> 'Revolution'\n"
        "  'Here Comes The Sun (2019 Mix)' -> 'Here Comes The Sun'\n"
        "  'While My Guitar Gently Weeps (2018 Mix)' -> "
        "'While My Guitar Gently Weeps'\n"
        "  'Hey Jude (Live)' -> 'Hey Jude (Live)'\n"
        "  'Blackbird (Acoustic)' -> 'Blackbird (Acoustic)'\n"
        "  'Bury Me' -> 'Bury Me'\n\n"
        f"Title: {raw_title}\n"
    )
