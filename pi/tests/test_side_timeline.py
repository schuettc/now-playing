"""The side timeline's rules, checked against the logged play of Failure,
*Fantastic Planet* (release 31427573) on 2026-11-20. Lengths are the Pi's
Discogs listing; a `silent` edge is logged five seconds after the quiet
began, so `quiet_began_at` is the logged time minus 5 s.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nowplaying.orchestrator import side_timeline as st
from nowplaying.orchestrator.side_timeline import Action

LENGTHS = {
    "A1": 268, "A2": 245, "A3": 114, "A4": 239,
    "B5": 130, "B6": 339, "B7": 77, "B8": 267, "B9": 223,
    "C10": 286, "C11": 186, "C12": 132, "C13": 266, "C14": 313,
    "D15": 269, "D16": 375, "D17": 362,
}
RELEASE_TRACKS = [
    {"position": p, "side": p[0], "title": p, "duration_seconds": d}
    for p, d in LENGTHS.items()
]
RID = 31427573


def at(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return datetime(2026, 11, 20, h, m, s, tzinfo=timezone.utc)


def timeline(position: str, started: str, tracks=RELEASE_TRACKS) -> st.SideTimeline:
    tl = st.start_at(tracks, position, at(started), RID, None)
    assert tl is not None
    return tl


def test_gap_inside_the_window_advances():
    tl = timeline("A1", "09:43:50")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("09:47:58"), at("09:48:10"))
    assert action is Action.ADVANCE
    assert (tl.position, tl.started_at) == ("A2", at("09:48:10"))


def test_run_out_with_no_gap_advances_at_the_listed_end():
    tl = timeline("A2", "09:48:10")
    assert st.on_tick(tl, at("09:52:20"), quiet=False) is Action.ADVANCE
    assert (tl.position, tl.started_at) == ("A3", at("09:52:15"))
    assert tl.by_run_out is True


def test_tick_before_the_listed_end_does_nothing():
    tl = timeline("A2", "09:48:10")
    assert st.on_tick(tl, at("09:52:05"), quiet=False) is Action.NONE
    assert tl.position == "A2"


def test_gap_after_a_run_out_retimes_and_does_not_advance_again():
    tl = timeline("B8", "10:07:41")
    assert st.on_tick(tl, at("10:12:10"), quiet=False) is Action.ADVANCE
    assert tl.position == "B9"
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("10:12:10"), at("10:12:25"))
    assert action is Action.RETIME
    assert (tl.position, tl.started_at) == ("B9", at("10:12:25"))
    assert tl.by_run_out is False


def test_long_quiet_spanning_a_run_out_boundary_retimes():
    tl = timeline("D16", "10:42:12")
    assert st.on_tick(tl, at("10:48:30"), quiet=False) is Action.ADVANCE
    assert tl.position == "D17"
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("10:48:23"), at("10:50:21"))
    assert action is Action.RETIME
    assert (tl.position, tl.started_at) == ("D17", at("10:50:21"))


def test_no_run_out_advance_while_quiet():
    tl = timeline("C12", "10:24:50")
    assert st.on_tick(tl, at("10:27:10"), quiet=True) is Action.NONE
    assert tl.position == "C12"


def test_gap_far_from_any_boundary_is_ignored():
    tl = timeline("C13", "10:27:51")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("10:30:44"), at("10:30:57"))
    assert action is Action.IGNORE
    assert (tl.position, tl.started_at) == ("C13", at("10:27:51"))


def test_short_dip_inside_the_window_advances():
    tl = timeline("B7", "10:06:49")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, None, at("10:07:41"))
    assert action is Action.ADVANCE
    assert tl.position == "B8"


def test_long_quiet_overlapping_the_window_advances():
    tl = timeline("C12", "10:24:50")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("10:26:13"), at("10:27:51"))
    assert action is Action.ADVANCE
    assert tl.position == "C13"


def test_quiet_on_the_last_track_is_a_flip():
    tl = timeline("B9", "10:12:25")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("10:15:55"), at("10:16:28"))
    assert action is Action.FLIP
    assert (tl.position, tl.side, tl.started_at) == ("C10", "C", at("10:16:28"))
    assert [t["position"] for t in tl.tracks] == ["C10", "C11", "C12", "C13", "C14"]


def test_short_dip_on_the_last_track_is_not_a_flip():
    tl = timeline("B9", "10:12:25")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, None, at("10:14:00"))
    assert action is Action.IGNORE
    assert tl.position == "B9"


def test_quiet_on_the_last_track_of_the_last_side_is_ignored():
    tl = timeline("D17", "10:50:21")
    action = st.on_sound_returned(tl, RELEASE_TRACKS, at("10:54:04"), at("10:54:40"))
    assert action is Action.IGNORE
    assert tl.position == "D17"


def test_last_track_stays_when_its_time_is_up():
    tl = timeline("B9", "10:12:25")
    assert st.on_tick(tl, at("10:16:20"), quiet=False) is Action.NONE
    assert tl.position == "B9"


def test_track_with_no_length_advances_on_any_gap():
    tracks = [dict(t) for t in RELEASE_TRACKS]
    tracks[0]["duration_seconds"] = None
    tl = timeline("A1", "09:43:50", tracks)
    assert tl.expected_end() is None
    action = st.on_sound_returned(tl, tracks, at("09:44:30"), at("09:44:40"))
    assert action is Action.ADVANCE
    assert tl.position == "A2"


def test_window_is_capped_at_half_the_shorter_track():
    tracks = [
        {"position": "A1", "side": "A", "title": "a", "duration_seconds": 40},
        {"position": "A2", "side": "A", "title": "b", "duration_seconds": 300},
    ]
    tl = timeline("A1", "09:00:00", tracks)
    assert st.boundary_window_s(tl.tracks, 0) == 20.0
    # 09:00:40 is the listed end; 21 s before it is outside the window.
    assert st.on_sound_returned(tl, tracks, None, at("09:00:19")) is Action.IGNORE
    assert st.on_sound_returned(tl, tracks, None, at("09:00:21")) is Action.ADVANCE


def test_side_end_is_known_only_when_every_remaining_length_is():
    tl = timeline("C13", "10:27:51")
    assert tl.side_expected_end() == at("10:27:51") + timedelta(seconds=266 + 313)
    assert tl.side_ended(at("10:30:00")) is False
    assert tl.side_ended(at("10:38:00")) is True
    tracks = [dict(t) for t in RELEASE_TRACKS]
    tracks[-4]["duration_seconds"] = None  # C14
    unknown = timeline("C13", "10:27:51", tracks)
    assert unknown.side_expected_end() is None
    assert unknown.side_ended(at("12:00:00")) is False


def test_legacy_track_position_key_is_accepted():
    tracks = [
        {"track_position": "A1", "side": "A", "duration_seconds": 100},
        {"track_position": "A2", "side": "A", "duration_seconds": 100},
    ]
    tl = st.start_at(tracks, "A2", at("09:00:00"), None, "mbid-1")
    assert tl is not None and tl.position == "A2" and tl.index == 1
    assert st.start_at(tracks, "B1", at("09:00:00"), None, "mbid-1") is None


# Logged edges, 2026-11-20: (`silent` logged time or None, `audible` time).
EDGES = [
    ("09:48:03", "09:48:10"), ("09:53:47", "09:54:05"), ("09:57:56", "09:58:43"),
    (None, "10:01:07"), ("10:06:31", "10:06:49"), (None, "10:07:41"),
    ("10:12:15", "10:12:25"), ("10:16:00", "10:16:28"), ("10:20:49", "10:21:13"),
    ("10:24:22", "10:24:50"), ("10:26:18", "10:27:51"), ("10:30:48", "10:30:57"),
    ("10:35:48", "10:37:43"), ("10:48:28", "10:50:21"), ("10:54:09", None),
]
# (when Shazam matched, position, the start the match supplied or None).
MATCHES = [
    ("09:44:02", "A1", "09:43:50"), ("09:54:06", "A4", None),
    ("09:58:44", "B5", None), ("10:27:51", "C13", None),
    ("10:30:58", "C14", None), ("10:37:43", "D15", None),
]
EXPECTED_STARTS = {
    "A2": "09:48:10", "A3": "09:52:15", "A4": "09:54:05", "B5": "09:58:43",
    "B6": "10:01:07", "B7": "10:06:49", "B8": "10:07:41", "B9": "10:12:25",
    "C10": "10:16:28", "C11": "10:21:13", "C12": "10:24:50", "C13": "10:27:51",
    "C14": "10:30:58", "D15": "10:37:43", "D16": "10:42:12", "D17": "10:50:21",
}


def _replay(tick_phase_s: int) -> tuple[list[str], dict[str, datetime]]:
    events: list[tuple[datetime, int, str, object]] = []
    for silent, audible in EDGES:
        if silent:
            events.append((at(silent), 0, "silent", None))
        if audible:
            events.append((at(audible), 1, "audible", None))
    for when, pos, start in MATCHES:
        events.append((at(when), 2, "match", (pos, at(start) if start else None)))
    tick = at("09:44:00") + timedelta(seconds=tick_phase_s)
    while tick < at("10:55:00"):
        events.append((tick, 3, "tick", None))
        tick += timedelta(seconds=15)
    events.sort(key=lambda e: (e[0], e[1]))

    tl: st.SideTimeline | None = None
    quiet_began_at: datetime | None = None
    order: list[str] = []
    starts: dict[str, datetime] = {}

    def note() -> None:
        if tl is None:
            return
        if not order or order[-1] != tl.position:
            order.append(tl.position)
        starts[tl.position] = tl.started_at

    for now, _, kind, data in events:
        if kind == "silent":
            quiet_began_at = now - timedelta(seconds=5)
        elif kind == "audible":
            if tl is not None:
                st.on_sound_returned(tl, RELEASE_TRACKS, quiet_began_at, now)
            quiet_began_at = None
        elif kind == "tick":
            if tl is not None:
                st.on_tick(tl, now, quiet=quiet_began_at is not None)
        else:
            pos, supplied = data
            if tl is not None and tl.position == pos:
                if supplied is not None:
                    tl.started_at = supplied
            else:
                tl = st.start_at(RELEASE_TRACKS, pos, supplied or now, RID, None)
        note()
    return order, starts


def test_replay_of_the_2026_11_20_play_places_all_seventeen_tracks():
    for phase in range(15):
        order, starts = _replay(phase)
        assert order == list(LENGTHS), f"tick phase {phase}"
        for pos, expected in EXPECTED_STARTS.items():
            off = abs((starts[pos] - at(expected)).total_seconds())
            assert off <= 2, f"{pos} started {starts[pos]} (tick phase {phase})"
