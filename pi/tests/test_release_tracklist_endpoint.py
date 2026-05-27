"""Tests for GET /api/release/{release_id}/tracklist.

Covers:
  - 200 with correct track shape for a known release
  - 200 with empty tracks list when release exists but has no tracks
  - 404 when release_id is not in the catalog
  - 400 for non-integer release_id
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control.search import release_tracklist  # noqa: E402
from nowplaying.discogs import catalog as discogs_catalog  # noqa: E402


@contextmanager
def _in_memory_db(releases: list[int], tracks: list[tuple]):
    """Build a temporary in-memory SQLite DB with releases and tracks tables.

    releases: list of release_ids to insert
    tracks: list of (release_id, position, side, title, duration_seconds)
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE releases ("
        "  id INTEGER PRIMARY KEY,"
        "  artist TEXT,"
        "  title TEXT,"
        "  year INTEGER,"
        "  label TEXT,"
        "  catno TEXT"
        ")"
    )
    con.execute(
        "CREATE TABLE tracks ("
        "  release_id INTEGER NOT NULL,"
        "  position TEXT NOT NULL,"
        "  side TEXT,"
        "  title TEXT,"
        "  duration_seconds INTEGER,"
        "  PRIMARY KEY (release_id, position)"
        ")"
    )
    for rid in releases:
        con.execute(
            "INSERT INTO releases (id, artist, title) VALUES (?, ?, ?)",
            (rid, f"Artist {rid}", f"Album {rid}"),
        )
    for row in tracks:
        con.execute(
            "INSERT INTO tracks (release_id, position, side, title, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?)",
            row,
        )
    con.commit()
    try:
        yield con
    finally:
        con.close()


def _mk_request(release_id_str: str) -> web.Request:
    req = MagicMock(spec=web.Request)
    req.match_info = {"release_id": release_id_str}
    return req


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_tracks_for_known_release():
    """200: correct track shape for a release that exists with tracks."""
    track_rows = [
        (100, "A1", "A", "First Track", 180),
        (100, "A2", "A", "Second Track", 240),
        (100, "B1", "B", "Third Track", None),
    ]
    with _in_memory_db(releases=[100], tracks=track_rows) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            req = _mk_request("100")
            resp = await release_tracklist(req)

    assert resp.status == 200
    body = resp.body if isinstance(resp.body, dict) else __import__("json").loads(resp.body)
    # aiohttp json_response serialises to bytes; decode via the response object
    import json
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert len(data["tracks"]) == 3
    first = data["tracks"][0]
    assert first["position"] == "A1"
    assert first["side"] == "A"
    assert first["title"] == "First Track"
    assert first["duration_seconds"] == 180
    last = data["tracks"][2]
    assert last["duration_seconds"] is None


@pytest.mark.asyncio
async def test_returns_empty_tracks_for_release_with_no_tracks():
    """200: empty tracks list when the release exists but has no track rows."""
    with _in_memory_db(releases=[200], tracks=[]) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            req = _mk_request("200")
            resp = await release_tracklist(req)

    import json
    data = json.loads(resp.body)
    assert resp.status == 200
    assert data["ok"] is True
    assert data["tracks"] == []


@pytest.mark.asyncio
async def test_404_for_unknown_release():
    """404 when release_id is not in the catalog."""
    with _in_memory_db(releases=[], tracks=[]) as con:
        with patch.object(discogs_catalog, "open_ro", return_value=con):
            req = _mk_request("999")
            resp = await release_tracklist(req)

    import json
    data = json.loads(resp.body)
    assert resp.status == 404
    assert data["ok"] is False
    assert "not found" in data["error"]


@pytest.mark.asyncio
async def test_400_for_non_integer_release_id():
    """400 when the release_id path segment is not a valid integer."""
    req = _mk_request("not-a-number")
    resp = await release_tracklist(req)

    import json
    data = json.loads(resp.body)
    assert resp.status == 400
    assert data["ok"] is False
    assert "integer" in data["error"]


@pytest.mark.asyncio
async def test_400_for_empty_release_id():
    """400 when the release_id path segment is empty string."""
    req = _mk_request("")
    resp = await release_tracklist(req)

    import json
    data = json.loads(resp.body)
    assert resp.status == 400
    assert data["ok"] is False
