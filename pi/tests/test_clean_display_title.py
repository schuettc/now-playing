"""Central display-title cleaning at the publish choke point.

Every vinyl publish routes through `_anchor_and_publish`, which calls
`_apply_clean_display_title` so all cascade branches (recognize, F3/F4
fingerprint, predicted-advance, needs-id) show the cleaned title — not
just the few payload builders that were patched individually.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.orchestrator._publish_enrichment import _apply_clean_display_title


def test_overwrites_with_clean_title():
    p = {
        "track_position": "A2",
        "title": "Penny Lane (2017 Mix)",
        "tracklist": [
            {"position": "A2", "title": "Penny Lane (2017 Mix)", "clean_title": "Penny Lane"},
        ],
    }
    _apply_clean_display_title(p)
    assert p["title"] == "Penny Lane"


def test_no_clean_title_leaves_raw():
    p = {
        "track_position": "A2",
        "title": "Bury Me",
        "tracklist": [{"position": "A2", "title": "Bury Me"}],  # clean_title absent
    }
    _apply_clean_display_title(p)
    assert p["title"] == "Bury Me"


def test_no_track_position_is_noop():
    p = {"title": "X (2017 Mix)", "tracklist": [{"position": "A1", "clean_title": "Y"}]}
    _apply_clean_display_title(p)
    assert p["title"] == "X (2017 Mix)"


def test_no_matching_entry_is_noop():
    p = {
        "track_position": "Z9",
        "title": "X (2017 Mix)",
        "tracklist": [{"position": "A1", "clean_title": "Y"}],
    }
    _apply_clean_display_title(p)
    assert p["title"] == "X (2017 Mix)"


def test_matches_track_position_key_too():
    # predicted/overlay tracklists may key entries by "track_position"
    p = {
        "track_position": "A2",
        "title": "Penny Lane (2017 Mix)",
        "tracklist": [{"track_position": "A2", "clean_title": "Penny Lane"}],
    }
    _apply_clean_display_title(p)
    assert p["title"] == "Penny Lane"


def test_no_tracklist_is_noop():
    p = {"track_position": "A2", "title": "Penny Lane (2017 Mix)"}
    _apply_clean_display_title(p)
    assert p["title"] == "Penny Lane (2017 Mix)"
