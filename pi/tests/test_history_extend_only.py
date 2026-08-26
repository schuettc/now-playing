"""History must not insert a fresh row for a publish the broadcaster
already declared content-identical.

The broadcaster de-dups at the WebSocket layer, but history.record_play
runs unconditionally on the next line with its own 60 s coalesce window
— sized for the 15 s vinyl heartbeat. AirPlay's mid-track NOTIFY cadence
is ~120 s, so every mid-track event landed outside the window and
inserted a duplicate row (6,450 of 10,878 airplay rows live).

The fix threads the broadcaster's own verdict through: publish() returns
whether it actually published, and a suppressed publish records
extend-only — extend the existing row's ended_at, never insert.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from nowplaying.api.broadcaster import Broadcaster
from nowplaying.history import db as histdb


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(histdb, "DB_PATH", tmp_path / "play_history.sqlite")
    histdb.init_db()
    return histdb


def _payload(**over):
    base = {
        "state": "PLAYING",
        "source": "airplay",
        "title": "Crooked Teeth",
        "artist": "Death Cab for Cutie",
        "album": "Plans",
        "release_id": 3112846,
        "track_position": "B4",
        "match_method": "sonos-polled",
        "art_url": "/art-cache/x",
    }
    base.update(over)
    return base


# ── broadcaster returns whether it published ──────────────────────────────

def test_publish_returns_false_when_suppressed():
    b = Broadcaster()
    assert _run(b.publish(_payload())) is True
    assert _run(b.publish(_payload())) is False


def test_publish_returns_true_when_content_changes():
    b = Broadcaster()
    assert _run(b.publish(_payload())) is True
    assert _run(b.publish(_payload(track_position="B5"))) is True


# ── history extend_only ───────────────────────────────────────────────────

def _row_count():
    with histdb._conn() as c:
        return c.execute("SELECT COUNT(*) FROM plays").fetchone()[0]


def _last_row():
    with histdb._conn() as c:
        return c.execute(
            "SELECT * FROM plays ORDER BY id DESC LIMIT 1"
        ).fetchone()


def test_extend_only_extends_row_beyond_coalesce_window(temp_db):
    """The core regression: a same-track publish 130 s later (past the
    60 s window) must extend, not insert."""
    _run(histdb.record_play(_payload()))
    assert _row_count() == 1
    start_end = _last_row()["ended_at"]
    with mock.patch("nowplaying.history.db.time.time", return_value=start_end + 130):
        _run(histdb.record_play(_payload(), extend_only=True))
    assert _row_count() == 1
    assert _last_row()["ended_at"] == start_end + 130


def test_extend_only_never_inserts_on_identity_mismatch(temp_db):
    """Stale-cache-at-track-change: extend_only for a DIFFERENT track than
    the last row must write nothing (never a spurious insert)."""
    _run(histdb.record_play(_payload(title="Track A")))
    assert _row_count() == 1
    _run(histdb.record_play(_payload(title="Track B"), extend_only=True))
    assert _row_count() == 1
    assert _last_row()["title"] == "Track A"


def test_extend_only_respects_clock_jump_guard(temp_db):
    """Clock moved backward → never write ended_at < started_at."""
    _run(histdb.record_play(_payload()))
    end = _last_row()["ended_at"]
    with mock.patch("nowplaying.history.db.time.time", return_value=end - 50):
        _run(histdb.record_play(_payload(), extend_only=True))
    assert _row_count() == 1
    row = _last_row()
    assert row["ended_at"] >= row["started_at"]
    assert row["ended_at"] == end


def test_record_play_default_still_inserts_after_window(temp_db):
    """Default extend_only=False keeps today's behaviour at the six other
    call sites: a same-track publish past the window inserts."""
    _run(histdb.record_play(_payload()))
    end = _last_row()["ended_at"]
    with mock.patch("nowplaying.history.db.time.time", return_value=end + 130):
        _run(histdb.record_play(_payload()))
    assert _row_count() == 2
