"""Tests for the MusicBrainz duration enrichment fallback.

Covers:
  - fetch_release_recordings parses the MusicBrainz API response correctly
  - null `length` fields are surfaced as None, not 0
  - HTTP errors / transport failures return None (caller distinguishes
    "MB had no data" from "we couldn't reach MB")
  - _enrich_durations_from_musicbrainz_async only fills NULL durations
  - resolved MBID gets cached on releases.musicbrainz_mbid
  - tracklist-count mismatch > 1 skips enrichment entirely
  - missing artist/title in releases row → skip without crash
  - no MBID match → graceful return, no UPDATEs

See docs/features/musicbrainz-duration-enrichment/.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))
_SCRIPTS_ROOT = _PI_ROOT / "scripts"
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))


def _run(coro):
    return asyncio.run(coro)


def _make_db_with_release(
    *,
    release_id: int = 12345,
    artist: str = "J Dilla",
    title: str = "Donuts",
    mbid: str | None = None,
    tracks: list[tuple[str, str, int | None]] | None = None,
) -> sqlite3.Connection:
    """Build an in-memory DB matching the discogs_sync schema with one
    release and the given (position, title, duration) tracks."""
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute("""
        CREATE TABLE releases (
            id INTEGER PRIMARY KEY,
            artist TEXT,
            title TEXT,
            musicbrainz_mbid TEXT
        )
    """)
    con.execute("""
        CREATE TABLE tracks (
            release_id INTEGER NOT NULL,
            position TEXT,
            side TEXT,
            title TEXT,
            duration_seconds INTEGER,
            clean_title TEXT,
            PRIMARY KEY (release_id, position)
        )
    """)
    con.execute(
        "INSERT INTO releases (id, artist, title, musicbrainz_mbid) VALUES (?, ?, ?, ?)",
        (release_id, artist, title, mbid),
    )
    for pos, ttl, dur in (tracks or []):
        con.execute(
            "INSERT INTO tracks (release_id, position, side, title, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?)",
            (release_id, pos, pos[0] if pos else None, ttl, dur),
        )
    return con


# ---------------------------------------------------------------------------
# fetch_release_recordings parsing
# ---------------------------------------------------------------------------

class _MockResp:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._payload


class _MockSession:
    def __init__(self, resp: _MockResp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, headers=None):
        return self._resp


def _patch_session(payload: dict, status: int = 200):
    """Patch aiohttp.ClientSession to return a fixed payload."""
    return patch(
        "nowplaying.coverart.aiohttp.ClientSession",
        return_value=_MockSession(_MockResp(status, payload)),
    )


def test_fetch_release_recordings_parses_length_field():
    """Happy path: MusicBrainz returns recordings with `length` in ms;
    we convert to seconds."""
    from nowplaying import coverart

    payload = {
        "media": [
            {
                "tracks": [
                    {"title": "Donuts (outro)", "length": 12700},
                    {"title": "Workinonit", "length": 177100},
                    {"title": "Waves", "length": 98500},
                ],
            },
        ],
    }
    with _patch_session(payload):
        recordings = _run(coverart.fetch_release_recordings("test-mbid"))

    assert recordings is not None
    assert len(recordings) == 3
    assert recordings[0]["title"] == "Donuts (outro)"
    # Python's banker's rounding: 12.7s → 13, 177.1s → 177, 98.5s → 98 (even)
    assert recordings[0]["duration_seconds"] == 13
    assert recordings[1]["duration_seconds"] == 177
    assert recordings[2]["duration_seconds"] == 98


def test_fetch_release_recordings_null_length_yields_none():
    """A track whose `length` field is null (MusicBrainz doesn't know) is
    surfaced as duration_seconds=None, not 0 — caller must distinguish."""
    from nowplaying import coverart

    payload = {
        "media": [
            {"tracks": [
                {"title": "Unknown Length", "length": None},
                {"title": "Real Track", "length": 60000},
            ]},
        ],
    }
    with _patch_session(payload):
        recordings = _run(coverart.fetch_release_recordings("test-mbid"))

    assert recordings is not None
    assert recordings[0]["duration_seconds"] is None
    assert recordings[1]["duration_seconds"] == 60


def test_fetch_release_recordings_handles_missing_media():
    """A release with no `media` array returns empty list, not None."""
    from nowplaying import coverart

    with _patch_session({}):
        recordings = _run(coverart.fetch_release_recordings("test-mbid"))
    assert recordings == []


def test_fetch_release_recordings_handles_http_error():
    """Non-200 status returns None so caller distinguishes 'no data' (empty
    list) from 'fetch failed' (None)."""
    from nowplaying import coverart

    with _patch_session({}, status=503):
        recordings = _run(coverart.fetch_release_recordings("test-mbid"))
    assert recordings is None


def test_fetch_release_recordings_empty_mbid_returns_none():
    """Defensive: empty MBID short-circuits without a network call."""
    from nowplaying import coverart

    recordings = _run(coverart.fetch_release_recordings(""))
    assert recordings is None


# ---------------------------------------------------------------------------
# _enrich_durations_from_musicbrainz_async — fill behavior
# ---------------------------------------------------------------------------

def test_enrich_only_fills_nulls():
    """Pre-existing Discogs durations are preserved; only NULL rows
    are updated from MusicBrainz."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",  # skip MBID resolution
        tracks=[
            ("A1", "Track 1", 60),     # populated — must NOT be overwritten
            ("A2", "Track 2", None),   # NULL — must be filled
            ("A3", "Track 3", None),   # NULL — must be filled
        ],
    )
    mb_recordings = [
        {"title": "Track 1", "duration_seconds": 999},  # different from Discogs
        {"title": "Track 2", "duration_seconds": 120},
        {"title": "Track 3", "duration_seconds": 180},
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 2
    rows = dict(con.execute(
        "SELECT position, duration_seconds FROM tracks WHERE release_id=12345",
    ).fetchall())
    assert rows["A1"] == 60, "existing Discogs duration must be preserved"
    assert rows["A2"] == 120
    assert rows["A3"] == 180


def test_enrich_caches_mbid():
    """First call resolves MBID via candidate search; subsequent calls
    reuse the cached value without searching again."""
    import discogs_sync

    con = _make_db_with_release(
        mbid=None,  # not yet resolved
        tracks=[("A1", "Track 1", None)],
    )
    with patch(
        "nowplaying.coverart.search_release_candidates",
        return_value=[("resolved-mbid-abc", "rg-xyz")],
    ) as mock_search, patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=[{"title": "Track 1", "duration_seconds": 60}],
    ):
        _run(discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345))

    mock_search.assert_called_once()
    cached = con.execute(
        "SELECT musicbrainz_mbid FROM releases WHERE id=12345",
    ).fetchone()[0]
    assert cached == "resolved-mbid-abc"

    # Second pass: leave a fresh NULL row, run again, search must NOT be
    # called again (MBID is cached).
    con.execute(
        "INSERT INTO tracks (release_id, position, title, duration_seconds) "
        "VALUES (12345, 'A2', 'Track 2', NULL)",
    )
    with patch(
        "nowplaying.coverart.search_release_candidates",
        return_value=[("should-not-be-called", None)],
    ) as mock_search2, patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=[
            {"title": "Track 1", "duration_seconds": 60},
            {"title": "Track 2", "duration_seconds": 120},
        ],
    ):
        _run(discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345))

    mock_search2.assert_not_called()


def test_resolve_picks_best_track_count_match():
    """Regression for Donuts 2026-05-21 live test: MusicBrainz returns
    multiple candidates for the same album (e.g. 2006 original 31-track,
    2013 reissue 35-track, anniversary edition). The first candidate
    isn't always the right one. Resolver must walk candidates and pick
    the one whose track count matches Discogs.
    """
    import discogs_sync

    con = _make_db_with_release(
        mbid=None,
        tracks=[(f"A{i+1}", f"Track {i+1}", None) for i in range(31)],
    )
    # First candidate is the reissue with bonus tracks; second is the
    # original pressing. The expected behavior: pick the second.
    candidates = [
        ("reissue-mbid-2013", None),
        ("original-mbid-2006", None),
        ("anniversary-mbid", None),
    ]

    async def _recordings_for(mbid, **kw):
        if mbid == "reissue-mbid-2013":
            return [
                {"title": f"Track {i+1}", "duration_seconds": 60}
                for i in range(35)
            ]
        if mbid == "original-mbid-2006":
            return [
                {"title": f"Track {i+1}", "duration_seconds": 90}
                for i in range(31)
            ]
        return [{"title": "x", "duration_seconds": 1}] * 40

    async def _search_for_candidates(*args, **kw):
        return candidates

    with patch(
        "nowplaying.coverart.search_release_candidates",
        side_effect=_search_for_candidates,
    ), patch(
        "nowplaying.coverart.fetch_release_recordings",
        side_effect=_recordings_for,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    cached = con.execute(
        "SELECT musicbrainz_mbid FROM releases WHERE id=12345",
    ).fetchone()[0]
    assert cached == "original-mbid-2006", (
        "must pick the candidate whose track count matches Discogs (31), "
        "not the reissue (35)"
    )
    # All 31 NULL durations filled from the matching pressing's recordings
    assert updated == 31


def test_resolve_aborts_when_no_candidate_within_one():
    """If every MusicBrainz candidate has a track count >1 off from
    Discogs, abort entirely — never pick the 'least-bad' wrong pressing."""
    import discogs_sync

    con = _make_db_with_release(
        mbid=None,
        tracks=[(f"A{i+1}", f"Track {i+1}", None) for i in range(31)],
    )

    async def _recordings_for(mbid, **kw):
        # All candidates have wildly different track counts
        return [{"title": "x", "duration_seconds": 60}] * 50

    async def _search(*args, **kw):
        return [("wrong-1", None), ("wrong-2", None)]

    with patch(
        "nowplaying.coverart.search_release_candidates",
        side_effect=_search,
    ), patch(
        "nowplaying.coverart.fetch_release_recordings",
        side_effect=_recordings_for,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 0
    cached = con.execute(
        "SELECT musicbrainz_mbid FROM releases WHERE id=12345",
    ).fetchone()[0]
    assert cached is None, "must NOT cache a wrong MBID"


def test_enrich_large_count_diff_with_title_matches_proceeds():
    """Title-matching replaces the old position-count abort: when MB
    returns far more tracks than Discogs but the relevant titles match,
    fill those. No more cross-pressing-contamination risk because
    title-match doesn't pollinate non-matching titles.
    """
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Track 1", None),
            ("A2", "Track 2", None),
        ],
    )
    # MusicBrainz returns 10 tracks; 2 of them are exact title matches.
    mb_recordings = [
        {"title": f"Track {i}", "duration_seconds": 60 * i}
        for i in range(10)
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    # Title-match found "Track 1" → 60 and "Track 2" → 120
    assert updated == 2
    rows = dict(con.execute(
        "SELECT position, duration_seconds FROM tracks WHERE release_id=12345",
    ).fetchall())
    assert rows["A1"] == 60
    assert rows["A2"] == 120


def test_enrich_large_count_diff_with_no_title_matches_fills_nothing():
    """Cross-pressing safety: when MB returns far more tracks but NONE
    of their titles match Discogs's tracks, nothing gets filled — the
    old position-count abort isn't needed because title-match has the
    same protective effect for unrelated tracklists.
    """
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Atrocity Exhibition", None),
            ("A2", "Isolation", None),
        ],
    )
    # 10 unrelated MB tracks (different album).
    mb_recordings = [
        {"title": f"Unrelated Song {i}", "duration_seconds": 60}
        for i in range(10)
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    # Zero title matches → zero fills, regardless of count diff.
    assert updated == 0


def test_enrich_off_by_one_with_title_match_works():
    """Hidden intro / bonus track that doesn't exist on Discogs side is
    correctly skipped via title-match; the rest of the album fills."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Track 1", None),
            ("A2", "Track 2", None),
            ("A3", "Track 3", None),
        ],
    )
    # 4 MB tracks: a hidden intro plus 3 that match the Discogs titles
    mb_recordings = [
        {"title": "Hidden Intro", "duration_seconds": 5},
        {"title": "Track 1", "duration_seconds": 60},
        {"title": "Track 2", "duration_seconds": 120},
        {"title": "Track 3", "duration_seconds": 180},
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 3
    rows = dict(con.execute(
        "SELECT position, duration_seconds FROM tracks WHERE release_id=12345",
    ).fetchall())
    assert rows["A1"] == 60
    assert rows["A2"] == 120
    assert rows["A3"] == 180


def test_enrich_no_mbid_match_returns_zero():
    """No MusicBrainz candidates for the artist/title pair → log +
    return 0. No UPDATEs, no crash, no MBID cache write."""
    import discogs_sync

    con = _make_db_with_release(
        mbid=None,
        tracks=[("A1", "Track 1", None)],
    )
    with patch(
        "nowplaying.coverart.search_release_candidates",
        return_value=[],
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 0
    cached = con.execute(
        "SELECT musicbrainz_mbid FROM releases WHERE id=12345",
    ).fetchone()[0]
    assert cached is None


def test_enrich_missing_artist_title_skips():
    """Defensive: if releases row is missing artist or title (incomplete
    basic sync), skip enrichment rather than search MusicBrainz with
    empty strings."""
    import discogs_sync

    con = _make_db_with_release(
        artist="",
        title="",
        mbid=None,
        tracks=[("A1", "Track 1", None)],
    )
    with patch(
        "nowplaying.coverart.search_release_candidates",
        return_value=[("should-not-be-called", None)],
    ) as mock_search:
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 0
    mock_search.assert_not_called()


def test_enrich_handles_fetch_recordings_none():
    """If fetch_release_recordings returns None (HTTP failure), no
    UPDATEs run — the MBID cache write (if any) still succeeded earlier."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[("A1", "Track 1", None)],
    )
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=None,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 0


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def test_open_db_adds_musicbrainz_mbid_column(tmp_path, monkeypatch):
    """_migrate_schema adds musicbrainz_mbid to existing releases tables
    that pre-date the column. Idempotent: re-running is safe."""
    import discogs_sync

    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr(discogs_sync, "DB_PATH", db_path)
    monkeypatch.setattr(discogs_sync, "DATA_DIR", tmp_path)

    # Simulate a pre-migration DB: create releases without the new column.
    con = sqlite3.connect(db_path, isolation_level=None)  # skylos: ignore SKY-L008 — Why: all three connections (con, con at line 574, con2) are explicitly closed immediately after use; skylos sees the name reuse as missing cleanup but the first con.close() is on the very next statement after the execute
    con.execute("""
        CREATE TABLE releases (
            id INTEGER PRIMARY KEY,
            artist TEXT,
            title TEXT
        )
    """)
    con.close()

    # First open_db: applies the ALTER TABLE migration.
    con = discogs_sync.open_db()
    cols = {row[1] for row in con.execute("PRAGMA table_info(releases)")}
    assert "musicbrainz_mbid" in cols
    con.close()

    # Second open_db on the migrated DB: no-op, no exception.
    con2 = discogs_sync.open_db()
    cols2 = {row[1] for row in con2.execute("PRAGMA table_info(releases)")}
    assert "musicbrainz_mbid" in cols2
    con2.close()


# ── Search normalization + HTML entities (Bug #3+#4) ─────────────────────


def test_normalize_album_title_strips_trailing_dots():
    """DJ Shadow "Endtroducing....." → "Endtroducing". MusicBrainz catalogs
    these without the trailing dots; literal search misses without the
    normalization."""
    from nowplaying import coverart
    assert coverart._normalize_album_title("Endtroducing.....") == "Endtroducing"
    assert coverart._normalize_album_title("Endtroducing...") == "Endtroducing"


def test_normalize_album_title_strips_trailing_question_mark():
    """Oasis "(What's The Story) Morning Glory?" → "(What's The Story)
    Morning Glory". The parenthetical is part of the canonical title;
    only the trailing question mark trips MusicBrainz's literal search.
    """
    from nowplaying import coverart
    result = coverart._normalize_album_title(
        "(What's The Story) Morning Glory?",
    )
    assert result == "(What's The Story) Morning Glory"


def test_normalize_album_title_strips_trailing_exclamation():
    from nowplaying import coverart
    assert (
        coverart._normalize_album_title("All My Friends Are Funeral Singers!")
        == "All My Friends Are Funeral Singers"
    )


def test_normalize_album_title_preserves_internal_punctuation():
    """Defensive: internal punctuation (commas, colons, internal dots)
    must NOT be stripped. Only trailing runs."""
    from nowplaying import coverart
    assert (
        coverart._normalize_album_title("Time: The Donut Of The Heart")
        == "Time: The Donut Of The Heart"
    )
    assert (
        coverart._normalize_album_title("E.S.P. (album)")
        == "E.S.P. (album)"
    )


def test_normalize_album_title_idempotent_when_no_match():
    """Returns input unchanged when no pattern matches — callers can
    cheaply detect a no-op."""
    from nowplaying import coverart
    title = "Donuts"
    assert coverart._normalize_album_title(title) is title or \
           coverart._normalize_album_title(title) == title


def test_search_release_candidates_unescapes_html_entities(monkeypatch):
    """Discogs occasionally emits HTML entities in artist/album fields
    ("Jon Langford &amp; His Fancy Men"). MusicBrainz won't match the
    literal &amp;; html.unescape must run before the search query is
    constructed.
    """
    import nowplaying.coverart as coverart
    captured_queries: list[str] = []

    class _FakeResp:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self):
            return {"releases": []}

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url, headers=None):
            captured_queries.append(url)
            return _FakeResp()

    monkeypatch.setattr(coverart.aiohttp, "ClientSession", lambda **kw: _FakeSession())

    import asyncio
    asyncio.run(
        coverart.search_release_candidates(
            "Jon Langford &amp; His Fancy Men",
            "Dark Matter Sessions",
        ),
    )
    assert captured_queries, "search should have been attempted"
    # First (literal) query string must contain the decoded &, not &amp;
    assert "%26amp%3B" not in captured_queries[0], (
        f"HTML entity leaked into query: {captured_queries[0]}"
    )
    assert "%26" in captured_queries[0], (
        "decoded ampersand should be URL-encoded as %26"
    )


def test_search_release_candidates_retries_with_normalized_title(monkeypatch):
    """When the literal title returns zero matches, retry with the
    normalized title. Caught live during the Donuts duration backfill —
    "Endtroducing....." and similar canonical titles missed the first
    pass but would have matched with normalization.
    """
    import nowplaying.coverart as coverart

    queries: list[str] = []
    # First call (literal) → empty. Second call (normalized) → one match.
    responses = iter([
        {"releases": []},
        {"releases": [{"id": "mbid-normalized", "release-group": {"id": "rg-1"}}]},
    ])

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self):
            return self._payload

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url, headers=None):
            queries.append(url)
            return _FakeResp(next(responses))

    monkeypatch.setattr(coverart.aiohttp, "ClientSession", lambda **kw: _FakeSession())

    import asyncio
    result = asyncio.run(
        coverart.search_release_candidates("DJ Shadow", "Endtroducing....."),
    )
    assert len(queries) == 2, "must retry with normalized title on empty result"
    # First query carries the literal title; second carries the normalized
    # one without the trailing dots.
    assert "Endtroducing....." in urllib.parse.unquote(queries[0])
    assert "Endtroducing" in urllib.parse.unquote(queries[1])
    assert "....." not in urllib.parse.unquote(queries[1])
    assert result == [("mbid-normalized", "rg-1")]


def test_search_release_candidates_skips_normalize_when_literal_hits(monkeypatch):
    """Regression: when the literal search returns matches, the
    normalized retry must NOT fire (avoid wasted API calls and avoid
    polluting matches with looser-criteria results)."""
    import nowplaying.coverart as coverart

    queries: list[str] = []

    class _FakeResp:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self):
            return {"releases": [{"id": "first", "release-group": {"id": "rg"}}]}

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url, headers=None):
            queries.append(url)
            return _FakeResp()

    monkeypatch.setattr(coverart.aiohttp, "ClientSession", lambda **kw: _FakeSession())

    import asyncio
    asyncio.run(
        coverart.search_release_candidates("DJ Shadow", "Endtroducing....."),
    )
    assert len(queries) == 1, (
        "must not retry with normalized title when literal already matched"
    )

import urllib.parse


# ── Title-keyed matching (Bug #6) ──────────────────────────────────────────


def test_norm_title_basic():
    """Lowercase, strip whitespace, strip trailing punctuation."""
    import discogs_sync as ds
    assert ds._norm_title("Track Title") == "track title"
    assert ds._norm_title("  Track Title  ") == "track title"
    assert ds._norm_title("Endtroducing.....") == "endtroducing"
    assert ds._norm_title("Morning Glory?") == "morning glory"
    assert ds._norm_title("All My Friends!") == "all my friends"


def test_norm_title_strips_discogs_subtrack_markers():
    """Discogs uses ``**`` and ``*`` to mark embedded subtracks
    (``**Transmission 1``). MusicBrainz catalogs the same recording
    without those markers."""
    import discogs_sync as ds
    assert ds._norm_title("**Transmission 1") == "transmission 1"
    assert ds._norm_title("**Hidden Track") == "hidden track"
    assert ds._norm_title("*Intro") == "intro"


def test_norm_title_collapses_internal_whitespace():
    """Multiple spaces, tabs, line breaks collapse to one space."""
    import discogs_sync as ds
    assert ds._norm_title("Time:  The Donut  Of The Heart") == "time: the donut of the heart"


def test_norm_title_empty_input():
    """Defensive: empty/None input returns empty string."""
    import discogs_sync as ds
    assert ds._norm_title("") == ""
    assert ds._norm_title(None) == ""


def test_build_title_map_simple():
    """Each unique title maps to its duration."""
    import discogs_sync as ds
    recordings = [
        {"title": "Track A", "duration_seconds": 60},
        {"title": "Track B", "duration_seconds": 120},
        {"title": "Track C", "duration_seconds": None},
    ]
    m = ds._build_title_duration_map(recordings)
    assert m["track a"] == 60
    assert m["track b"] == 120
    assert m["track c"] is None  # MB doesn't know length


def test_build_title_map_ambiguous_duplicate_titles():
    """Two recordings with the same normalized title → AMBIGUOUS sentinel
    so callers can't accidentally pick one."""
    import discogs_sync as ds
    recordings = [
        {"title": "Reprise", "duration_seconds": 60},
        {"title": "REPRISE", "duration_seconds": 90},
    ]
    m = ds._build_title_duration_map(recordings)
    assert m["reprise"] is ds._AMBIGUOUS_TITLE


def test_enrich_title_match_handles_structural_mismatch():
    """DJ Shadow Endtroducing scenario: 14 Discogs rows (with subtracks
    ``**Transmission 1``, etc.), 12 MusicBrainz tracks (without the
    embedded interludes). Title-match fills the 12 main tracks; the 2
    subtracks stay NULL.
    """
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Best Foot Forward", None),
            ("A2", "Building Steam", None),
            ("B1.a", "Changeling", None),
            ("B1.b", "**Transmission 1", None),  # subtrack — MB doesn't have it
            ("B2", "Stem", None),
        ],
    )
    mb_recordings = [
        {"title": "Best Foot Forward", "duration_seconds": 49},
        {"title": "Building Steam", "duration_seconds": 400},
        {"title": "Changeling", "duration_seconds": 471},
        {"title": "Stem", "duration_seconds": 557},
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 4, "4 main tracks filled, subtrack left NULL"
    rows = dict(con.execute(
        "SELECT position, duration_seconds FROM tracks WHERE release_id=12345",
    ).fetchall())
    assert rows["A1"] == 49
    assert rows["A2"] == 400
    assert rows["B1.a"] == 471
    assert rows["B1.b"] is None  # subtrack stayed NULL
    assert rows["B2"] == 557


def test_enrich_title_match_skips_ambiguous_duplicate_titles():
    """When MB has two recordings with the same title (e.g. live encore
    reprises), the title-map marks them ambiguous and the Discogs row
    with that title stays NULL — never guess between identical-titled
    recordings."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Reprise", None),
            ("A2", "Track 2", None),
        ],
    )
    mb_recordings = [
        {"title": "Reprise", "duration_seconds": 60},
        {"title": "Reprise", "duration_seconds": 90},  # duplicate title
        {"title": "Track 2", "duration_seconds": 120},
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 1, "ambiguous Reprise stays NULL; Track 2 fills"
    rows = dict(con.execute(
        "SELECT position, duration_seconds FROM tracks WHERE release_id=12345",
    ).fetchall())
    assert rows["A1"] is None
    assert rows["A2"] == 120


def test_enrich_title_match_skips_when_mb_length_is_null():
    """MB occasionally has a track but no length recorded — caller
    distinguishes "match with no usable duration" from "no match at all"
    and skips both."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[("A1", "Track 1", None)],
    )
    mb_recordings = [
        {"title": "Track 1", "duration_seconds": None},  # MB knows the track but not its length
    ]
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 0


# ── Resolver title-overlap scoring (Bug #6 final piece) ────────────────────


def test_resolve_picks_candidate_with_title_overlap_despite_count_diff(monkeypatch):
    """Regression: resolver previously aborted when no candidate's
    track count was within ±1 of Discogs. With title-overlap scoring,
    Pink Floyd Wish You Were Here (Discogs splits Shine On parts I-V
    into 5 rows; MusicBrainz keeps it as 1) resolves correctly because
    the title 'Welcome To The Machine' / 'Have A Cigar' / 'Wish You
    Were Here' overlap is strong.
    """
    import discogs_sync

    # Discogs has 9 rows (5 Shine On parts + 4 other tracks);
    # MusicBrainz has 5 tracks (Shine On as single).
    discogs_titles = [
        "Shine On You Crazy Diamond (Parts I-V)",
        "Shine On You Crazy Diamond Part 1",  # split row
        "Shine On You Crazy Diamond Part 2",
        "Welcome To The Machine",
        "Have A Cigar",
        "Wish You Were Here",
        "Shine On You Crazy Diamond Part 6",
        "Shine On You Crazy Diamond Part 7",
        "Shine On You Crazy Diamond Part 8",
    ]
    mb_recordings = [
        {"title": "Shine On You Crazy Diamond (Parts I-V)", "duration_seconds": 800},
        {"title": "Welcome To The Machine", "duration_seconds": 446},
        {"title": "Have A Cigar", "duration_seconds": 308},
        {"title": "Wish You Were Here", "duration_seconds": 334},
        {"title": "Shine On You Crazy Diamond (Parts VI-IX)", "duration_seconds": 753},
    ]
    candidates = [("correct-mbid", None)]

    async def _search(*args, **kw):
        return candidates
    async def _recordings(mbid, **kw):
        return mb_recordings

    monkeypatch.setattr("nowplaying.coverart.search_release_candidates", _search)
    monkeypatch.setattr("nowplaying.coverart.fetch_release_recordings", _recordings)

    result = _run(
        discogs_sync._resolve_best_matching_mbid(
            __import__("nowplaying.coverart", fromlist=["x"]),
            "Pink Floyd",
            "Wish You Were Here",
            discogs_titles,
            release_id=1862700,
        ),
    )
    # Despite 9 vs 5 track count diff (=4, well above the old guard's ±1),
    # 4 titles overlap exactly (Shine On Parts I-V, Welcome To The Machine,
    # Have A Cigar, Wish You Were Here) → resolver picks the right MBID.
    assert result == "correct-mbid"


def test_resolve_aborts_when_no_title_overlap(monkeypatch):
    """Cross-pressing safety: if no candidate has any title overlap with
    Discogs, abort — we'd be filling durations from a wrong release.
    Title-overlap replaces the count-diff guard as the cross-pressing
    safety mechanism.
    """
    import discogs_sync

    discogs_titles = ["Atrocity Exhibition", "Isolation", "Passover"]
    mb_recordings_wrong = [
        {"title": "Completely Different Song", "duration_seconds": 60},
        {"title": "Another Different One", "duration_seconds": 120},
    ]

    async def _search(*args, **kw):
        return [("wrong-mbid", None)]
    async def _recordings(mbid, **kw):
        return mb_recordings_wrong

    monkeypatch.setattr("nowplaying.coverart.search_release_candidates", _search)
    monkeypatch.setattr("nowplaying.coverart.fetch_release_recordings", _recordings)

    result = _run(
        discogs_sync._resolve_best_matching_mbid(
            __import__("nowplaying.coverart", fromlist=["x"]),
            "Joy Division",
            "Closer",
            discogs_titles,
            release_id=9999,
        ),
    )
    assert result is None


# ── Recording-level fallback (vinyl-only null lengths) ────────────────────


def test_fetch_recording_duration_happy_path():
    """Length in ms is converted to int seconds."""
    from nowplaying import coverart

    with _patch_session({"length": 222000}):
        dur = _run(coverart.fetch_recording_duration("rec-mbid"))
    assert dur == 222


def test_fetch_recording_duration_null_length():
    """A recording without a length recorded returns None."""
    from nowplaying import coverart

    with _patch_session({"length": None}):
        dur = _run(coverart.fetch_recording_duration("rec-mbid"))
    assert dur is None


def test_fetch_recording_duration_http_error():
    """Non-200 HTTP returns None."""
    from nowplaying import coverart

    with _patch_session({}, status=503):
        dur = _run(coverart.fetch_recording_duration("rec-mbid"))
    assert dur is None


def test_fetch_recording_duration_empty_mbid():
    """Defensive: empty MBID short-circuits without a network call."""
    from nowplaying import coverart

    dur = _run(coverart.fetch_recording_duration(""))
    assert dur is None


def test_fetch_release_recordings_surfaces_recording_mbid():
    """Each parsed track now exposes the embedded recording.id."""
    from nowplaying import coverart

    payload = {
        "media": [
            {"tracks": [
                {
                    "title": "Ms. Lazarus",
                    "length": None,
                    "recording": {"id": "rec-1", "length": None},
                },
                {
                    "title": "Comin' Home",
                    "length": 240000,
                    "recording": {"id": "rec-2", "length": 240000},
                },
                {
                    "title": "No Recording Field",
                    "length": 60000,
                },
            ]},
        ],
    }
    with _patch_session(payload):
        recordings = _run(coverart.fetch_release_recordings("test-mbid"))

    assert recordings is not None
    assert recordings[0]["recording_mbid"] == "rec-1"
    assert recordings[1]["recording_mbid"] == "rec-2"
    assert recordings[2]["recording_mbid"] is None


def test_enrich_recording_level_fills_when_release_level_null():
    """Vinyl-only Hum case: release-level lengths all null, but the
    canonical recording entity has the length. Recording-level fallback
    fills."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Isle of the Cheetah", None),
            ("A2", "Comin' Home", None),
        ],
    )
    # Release-level recordings: titles match Discogs, but lengths null.
    mb_recordings = [
        {"title": "Isle of the Cheetah", "duration_seconds": None,
         "recording_mbid": "rec-isle"},
        {"title": "Comin' Home", "duration_seconds": None,
         "recording_mbid": "rec-comin"},
    ]

    async def _fake_recording_duration(mbid, **kw):
        return {"rec-isle": 360, "rec-comin": 240}.get(mbid)

    async def _noop(*a, **kw):
        return None

    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ), patch(
        "nowplaying.coverart.fetch_recording_duration",
        side_effect=_fake_recording_duration,
    ), patch(
        "discogs._enrich.asyncio.sleep",
        side_effect=_noop,
    ):
        updated = _run(
            discogs_sync._enrich_durations_from_musicbrainz_async(con, 12345),
        )

    assert updated == 2
    rows = dict(con.execute(
        "SELECT position, duration_seconds FROM tracks WHERE release_id=12345",
    ).fetchall())
    assert rows["A1"] == 360
    assert rows["A2"] == 240


def test_enrich_recording_level_caches_within_sweep():
    """Same recording_mbid encountered twice (across releases or within a
    single release with duplicates) is fetched once. The cache stores None
    for null results too — don't re-fetch known-null recordings."""
    import discogs_sync

    con = _make_db_with_release(
        release_id=11111,
        mbid="mbid-1",
        tracks=[("A1", "Shared Track", None)],
    )
    con.execute(
        "INSERT INTO releases (id, artist, title, musicbrainz_mbid) "
        "VALUES (22222, 'Other', 'Other Album', 'mbid-2')",
    )
    con.execute(
        "INSERT INTO tracks (release_id, position, side, title, duration_seconds) "
        "VALUES (22222, 'A1', 'A', 'Shared Track', NULL)",
    )

    mb_recordings = [
        {"title": "Shared Track", "duration_seconds": None,
         "recording_mbid": "rec-shared"},
    ]
    call_count = {"n": 0}

    async def _fake_recording_duration(mbid, **kw):
        call_count["n"] += 1
        return 300

    async def _noop(*a, **kw):
        return None

    cache: dict[str, int | None] = {}
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ), patch(
        "nowplaying.coverart.fetch_recording_duration",
        side_effect=_fake_recording_duration,
    ), patch(
        "discogs._enrich.asyncio.sleep",
        side_effect=_noop,
    ):
        _run(discogs_sync._enrich_durations_from_musicbrainz_async(
            con, 11111, recording_cache=cache,
        ))
        _run(discogs_sync._enrich_durations_from_musicbrainz_async(
            con, 22222, recording_cache=cache,
        ))

    assert call_count["n"] == 1, "shared recording fetched exactly once"
    assert cache["rec-shared"] == 300
    rows = dict(con.execute(
        "SELECT release_id, duration_seconds FROM tracks",
    ).fetchall())
    assert rows[11111] == 300
    assert rows[22222] == 300


def test_enrich_recording_level_all_null_stays_null():
    """Both release-level and recording-level lengths null → row stays
    NULL gracefully. The cache records the None so we don't re-fetch."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[("A1", "Unknown Length Track", None)],
    )
    mb_recordings = [
        {"title": "Unknown Length Track", "duration_seconds": None,
         "recording_mbid": "rec-null"},
    ]

    async def _fake_recording_duration(mbid, **kw):
        return None  # recording entity ALSO has null length

    async def _noop(*a, **kw):
        return None

    cache: dict[str, int | None] = {}
    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ), patch(
        "nowplaying.coverart.fetch_recording_duration",
        side_effect=_fake_recording_duration,
    ), patch(
        "discogs._enrich.asyncio.sleep",
        side_effect=_noop,
    ):
        updated = _run(discogs_sync._enrich_durations_from_musicbrainz_async(
            con, 12345, recording_cache=cache,
        ))

    assert updated == 0
    assert cache == {"rec-null": None}, "None cached so we don't re-fetch"
    row = con.execute(
        "SELECT duration_seconds FROM tracks WHERE release_id=12345 AND position='A1'",
    ).fetchone()
    assert row[0] is None


def test_enrich_recording_level_skipped_when_release_level_fills():
    """Fast path stays fast: if the title-keyed map fills every NULL,
    no recording-level HTTP calls fire."""
    import discogs_sync

    con = _make_db_with_release(
        mbid="test-mbid",
        tracks=[
            ("A1", "Track 1", None),
            ("A2", "Track 2", None),
        ],
    )
    mb_recordings = [
        {"title": "Track 1", "duration_seconds": 60,
         "recording_mbid": "rec-1"},
        {"title": "Track 2", "duration_seconds": 120,
         "recording_mbid": "rec-2"},
    ]
    fetch_calls = {"n": 0}

    async def _fake_recording_duration(mbid, **kw):
        fetch_calls["n"] += 1
        return 999

    async def _noop(*a, **kw):
        return None

    with patch(
        "nowplaying.coverart.fetch_release_recordings",
        return_value=mb_recordings,
    ), patch(
        "nowplaying.coverart.fetch_recording_duration",
        side_effect=_fake_recording_duration,
    ), patch(
        "discogs._enrich.asyncio.sleep",
        side_effect=_noop,
    ):
        updated = _run(discogs_sync._enrich_durations_from_musicbrainz_async(
            con, 12345,
        ))

    assert updated == 2
    assert fetch_calls["n"] == 0, "recording-level not called when fast path fills"


def test_resolve_picks_smaller_count_diff_on_title_match_tie(monkeypatch):
    """When two candidates have the same number of title matches, prefer
    the one with smaller count diff (the original pressing over a reissue
    with bonus tracks).
    """
    import discogs_sync

    discogs_titles = [f"Track {i+1}" for i in range(31)]
    # Candidate 1: 31-track original (perfect match)
    # Candidate 2: 35-track reissue (same first 31 + 4 bonus)
    # Both have 31 title matches — tiebreak by count diff (0 vs 4).
    candidates = [("reissue-mbid", None), ("original-mbid", None)]

    async def _search(*args, **kw):
        return candidates
    async def _recordings(mbid, **kw):
        if mbid == "reissue-mbid":
            return [
                {"title": f"Track {i+1}", "duration_seconds": 60}
                for i in range(35)
            ]
        return [
            {"title": f"Track {i+1}", "duration_seconds": 60}
            for i in range(31)
        ]

    monkeypatch.setattr("nowplaying.coverart.search_release_candidates", _search)
    monkeypatch.setattr("nowplaying.coverart.fetch_release_recordings", _recordings)

    result = _run(
        discogs_sync._resolve_best_matching_mbid(
            __import__("nowplaying.coverart", fromlist=["x"]),
            "Some Artist",
            "Some Album",
            discogs_titles,
            release_id=12345,
        ),
    )
    assert result == "original-mbid"
