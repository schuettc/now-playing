"""The side timeline: which track of the side is current, when it started
and where the next boundary is expected. Pure rules: times come in as
arguments and an Action comes out; nothing here reads a clock, publishes
or logs.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

BOUNDARY_WINDOW_S = 30.0


class Action(enum.Enum):
    NONE = "none"
    IGNORE = "ignore"
    ADVANCE = "advance"
    RETIME = "retime"
    FLIP = "flip"


def _position(track: dict) -> str | None:
    return track.get("position") or track.get("track_position")


def _length_s(track: dict) -> float | None:
    d = track.get("duration_seconds")
    return float(d) if d else None


def boundary_window_s(tracks: list[dict], i: int) -> float:
    """Seconds either side of the boundary after ``tracks[i]`` in which a
    gap counts as that boundary: 30, capped at half of each listed length
    of the two tracks it separates."""
    w = BOUNDARY_WINDOW_S
    for j in (i, i + 1):
        if 0 <= j < len(tracks):
            d = _length_s(tracks[j])
            if d is not None:
                w = min(w, d / 2)
    return w


@dataclass
class SideTimeline:
    release_id: int | None
    release_mbid: str | None
    side: str
    tracks: list[dict]  # this side only, catalog order
    index: int  # current track in `tracks`
    started_at: datetime  # UTC, start of the current track
    by_run_out: bool = False  # made current by run-out and not yet retimed

    @property
    def position(self) -> str:
        return _position(self.tracks[self.index]) or ""

    def has_next(self) -> bool:
        return self.index + 1 < len(self.tracks)

    def expected_end(self) -> datetime | None:
        d = _length_s(self.tracks[self.index])
        if d is None:
            return None
        return self.started_at + timedelta(seconds=d)

    def side_expected_end(self) -> datetime | None:
        end = self.expected_end()
        if end is None:
            return None
        for t in self.tracks[self.index + 1:]:
            d = _length_s(t)
            if d is None:
                return None
            end += timedelta(seconds=d)
        return end

    def side_ended(self, now: datetime) -> bool:
        end = self.side_expected_end()
        return end is not None and now >= end

    def _advance(self, started_at: datetime, *, by_run_out: bool) -> None:
        self.index += 1
        self.started_at = started_at
        self.by_run_out = by_run_out


def start_at(
    release_tracks: list[dict], position: str, started_at: datetime,
    release_id: int | None, release_mbid: str | None,
) -> SideTimeline | None:
    """A timeline with ``position`` current, or None when the release's
    track list does not place it on a side."""
    row = next((t for t in release_tracks if _position(t) == position), None)
    if row is None or not row.get("side"):
        return None
    side = row["side"]
    tracks = [t for t in release_tracks if t.get("side") == side]
    return SideTimeline(
        release_id=release_id, release_mbid=release_mbid, side=side,
        tracks=tracks, index=tracks.index(row), started_at=started_at,
    )


def _next_side_tracks(tl: SideTimeline, release_tracks: list[dict]) -> list[dict]:
    """Tracks of the next distinct side in the release's track order."""
    seen_current = False
    for t in release_tracks:
        side = t.get("side")
        if side == tl.side:
            seen_current = True
        elif seen_current and side:
            return [x for x in release_tracks if x.get("side") == side]
    return []


def _overlaps(
    lo: datetime, hi: datetime, centre: datetime, window_s: float,
) -> bool:
    w = timedelta(seconds=window_s)
    return lo <= centre + w and hi >= centre - w


def _quiet_is_a_flip(tl: SideTimeline, quiet_began_at: datetime | None) -> bool:
    if quiet_began_at is None:
        return False
    if not tl.has_next():
        return True
    side_end = tl.side_expected_end()
    if side_end is None:
        return False
    w = boundary_window_s(tl.tracks, len(tl.tracks) - 1)
    return quiet_began_at >= side_end - timedelta(seconds=w)


def on_sound_returned(
    tl: SideTimeline, release_tracks: list[dict],
    quiet_began_at: datetime | None, now: datetime,
) -> Action:
    """Sound is back after the quiet ``[quiet_began_at, now]`` (a dip too
    short to be reported has ``quiet_began_at`` None). Mutates ``tl``."""
    lo = quiet_began_at if quiet_began_at is not None else now
    if tl.by_run_out and tl.index > 0 and _overlaps(
        lo, now, tl.started_at, boundary_window_s(tl.tracks, tl.index - 1),
    ):
        tl.started_at = now
        tl.by_run_out = False
        return Action.RETIME
    if _quiet_is_a_flip(tl, quiet_began_at):
        nxt = _next_side_tracks(tl, release_tracks)
        if not nxt:
            return Action.IGNORE
        tl.side = nxt[0]["side"]
        tl.tracks = nxt
        tl.index = 0
        tl.started_at = now
        tl.by_run_out = False
        return Action.FLIP
    if not tl.has_next():
        return Action.IGNORE
    end = tl.expected_end()
    if end is None or _overlaps(
        lo, now, end, boundary_window_s(tl.tracks, tl.index),
    ):
        tl._advance(now, by_run_out=False)
        return Action.ADVANCE
    return Action.IGNORE


def on_tick(tl: SideTimeline, now: datetime, quiet: bool) -> Action:
    """An unmatched heartbeat: when the current track's listed time is up
    and no quiet is in progress, the next track starts at that listed end.
    The last track of a side stays. Mutates ``tl``."""
    end = tl.expected_end()
    if quiet or end is None or now < end or not tl.has_next():
        return Action.NONE
    tl._advance(end, by_run_out=True)
    return Action.ADVANCE
