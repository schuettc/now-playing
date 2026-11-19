"""A paused-and-resumed track is one play, not two history rows.

Real sequence (AirPlay, 2026-11-10): pause, the 600 s idle timer fires,
the repoll re-announces the paused track as PLAYING, then the listener
resumes minutes later. Each PLAYING publish differs from the PAUSED one
before it, so none is extend-only, and each landed past the 60 s window.
"""
from __future__ import annotations

from unittest import mock

import pytest

from nowplaying.history import db as histdb


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(histdb, "DB_PATH", tmp_path / "play_history.sqlite")
    monkeypatch.setattr(histdb, "_paused_track", None)
    histdb.init_db()


def _payload(**over):
    base = {
        "state": "PLAYING",
        "source": "airplay",
        "title": "Comin' Home",
        "artist": "Hum",
        "album": "Downward Is Heavenward",
        "match_method": "sonos-polled",
    }
    base.update(over)
    return base


def _at(t: int, **over):
    with mock.patch.object(histdb.time, "time", return_value=t):
        return histdb._record_play_sync(_payload(**over))


def _rows():
    with histdb._conn() as c:
        return [tuple(r) for r in c.execute(
            "SELECT title, started_at, ended_at FROM plays ORDER BY id"
        )]


def test_pause_then_resume_extends_the_same_row():
    _at(1000)
    _at(1154, state="PAUSED_PLAYBACK")
    _at(1762)                            # repoll re-announces it as PLAYING
    _at(1763, state="PAUSED_PLAYBACK")
    _at(2116)                            # the real resume
    assert _rows() == [("Comin' Home", 1000, 2116)]


def test_replay_without_a_pause_still_gets_its_own_row():
    _at(1000)
    _at(1300)
    assert len(_rows()) == 2


def test_pause_of_another_track_does_not_bridge():
    _at(1000)
    _at(1010, state="PAUSED_PLAYBACK", title="Stars")
    _at(1300)
    assert len(_rows()) == 2


def test_resume_after_a_session_gap_is_a_new_play():
    _at(1000)
    _at(1010, state="PAUSED_PLAYBACK")
    _at(1010 + histdb.ALBUM_SESSION_GAP_S + 1)
    assert len(_rows()) == 2
