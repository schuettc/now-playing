from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_SCRIPTS = _PI_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import recognize_proto  # noqa: E402


def test_release_fields_tracklist_carries_clean_title():
    rel = {
        "id": 1, "artist": "A", "title": "Alb",
        "tracks": [
            {"position": "A1", "side": "A", "title": "Song (2017 Mix)",
             "duration_seconds": 180, "clean_title": "Song"},
        ],
    }
    fields = recognize_proto._release_fields(rel)
    entry = fields["tracklist"][0]
    assert entry["title"] == "Song (2017 Mix)"   # raw preserved (for matching)
    assert entry["clean_title"] == "Song"
