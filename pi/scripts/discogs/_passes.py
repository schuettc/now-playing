"""Sync passes: basic collection fetch, detail + tracklist, cover art, duration backfill."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import discogs_client
import requests

from nowplaying._io_safe import safe_write_bytes

from ._db import ART_DIR, DATA_DIR, REPO_ROOT, log, now_iso
from ._enrich import _maybe_enrich_durations, clean_release_titles
from ._helpers import (
    _parse_discogs_duration,
    iter_leaf_tracks,
    iter_suite_parents,
    join_artists,
    join_formats,
    join_labels,
    position_to_side,
    upsert_basic,
)

USER_AGENT = "now-playing/0.1 (+https://github.com/schuettc/now-playing)"


def fetch_detail(con: sqlite3.Connection, client: discogs_client.Client, release_id: int) -> None:
    rel = client.release(release_id)
    # Force fetch
    _ = rel.title
    detail = {
        "id": rel.id,
        "title": rel.title,
        "country": getattr(rel, "country", None),
        "year": rel.year,
        "released": getattr(rel, "released", None),
        "genres": list(getattr(rel, "genres", []) or []),
        "styles": list(getattr(rel, "styles", []) or []),
        "notes": getattr(rel, "notes", None),
    }
    tracklist = list(getattr(rel, "tracklist", []) or [])

    con.execute(
        """
        UPDATE releases
        SET country = ?,
            raw_detail_json = ?,
            detail_synced_at = ?
        WHERE id = ?
        """,
        (detail.get("country"), json.dumps(detail), now_iso(), release_id),
    )

    # Replace tracks for this release
    con.execute("DELETE FROM tracks WHERE release_id = ?", (release_id,))
    for position, title, duration_str in iter_leaf_tracks(tracklist):
        dur = _parse_discogs_duration(duration_str)
        con.execute(
            "INSERT OR REPLACE INTO tracks (release_id, position, side, title, duration_seconds, is_suite_parent) VALUES (?, ?, ?, ?, ?, 0)",
            (release_id, position, position_to_side(position), title, dur),
        )
    # Suite/medley parents: store the parent row (e.g. ``D1, "Homecoming"``)
    # so Shazam's parent-title match can resolve to a release + position
    # prefix. Marked is_suite_parent=1 so downstream readers filter them
    # out of the playable tracklist.
    for position, title, duration_str in iter_suite_parents(tracklist):
        dur = _parse_discogs_duration(duration_str)
        con.execute(
            "INSERT OR REPLACE INTO tracks (release_id, position, side, title, duration_seconds, is_suite_parent) VALUES (?, ?, ?, ?, ?, 1)",
            (release_id, position, position_to_side(position), title, dur),
        )

    # Title cleaning: strip remaster/mix annotations so clean_title holds
    # the canonical title used by Last.fm aggregation and MusicBrainz matching.
    # Runs before the duration backfill so Task 13 can match on clean_title.
    from nowplaying.llm import LLMAssist
    llm = LLMAssist()
    asyncio.run(clean_release_titles(con, release_id, llm=llm))

    # Duration enrichment: if Discogs left any track with a NULL duration,
    # try MusicBrainz as a fallback source. Same album, different provider.
    # See docs/features/musicbrainz-duration-enrichment/.
    _maybe_enrich_durations(con, release_id)


def fetch_art(con: sqlite3.Connection, release_id: int, url: str) -> None:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    if url.lower().endswith(".png"):
        ext = ".png"
    out = ART_DIR / f"{release_id}{ext}"
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)  # skylos: ignore SKY-D216 — admin script; url comes from Discogs API responses (img.discogs.com / i.discogs.com)
    r.raise_for_status()
    safe_write_bytes(out, r.content)
    rel_path = str(out.relative_to(REPO_ROOT))
    con.execute(
        "UPDATE releases SET art_path = ?, art_synced_at = ? WHERE id = ?",
        (rel_path, now_iso(), release_id),
    )


def pass_basic(con: sqlite3.Connection, client: discogs_client.Client, limit: int | None) -> None:
    me = client.identity()
    folder = me.collection_folders[0]  # "All"
    log(f"basic pass: collection has {folder.count} releases")
    items = folder.releases
    seen = 0
    for item in items:
        upsert_basic(con, item)
        seen += 1
        if seen % 25 == 0:
            log(f"  basic: {seen}/{folder.count}")
        if limit is not None and seen >= limit:
            break
    log(f"basic pass: synced {seen} releases")


def pass_details(con: sqlite3.Connection, client: discogs_client.Client, limit: int | None) -> None:
    rows = con.execute(
        "SELECT id FROM releases WHERE detail_synced_at IS NULL ORDER BY id"
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    log(f"details pass: {len(rows)} releases need tracklists")
    for i, (release_id,) in enumerate(rows, 1):
        try:
            fetch_detail(con, client, release_id)
        except Exception as e:
            log(f"  details: release {release_id} FAILED: {e!r}")
            continue
        if i % 10 == 0 or i == len(rows):
            log(f"  details: {i}/{len(rows)}")


def pass_art(con: sqlite3.Connection, limit: int | None) -> None:
    rows = con.execute(
        "SELECT id, primary_image_url FROM releases "
        "WHERE art_path IS NULL AND primary_image_url IS NOT NULL ORDER BY id"
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    log(f"art pass: {len(rows)} releases need cover art")
    for i, (release_id, url) in enumerate(rows, 1):
        try:
            fetch_art(con, release_id, url)
        except Exception as e:
            log(f"  art: release {release_id} FAILED: {e!r}")
            continue
        if i % 25 == 0 or i == len(rows):
            log(f"  art: {i}/{len(rows)}")
        time.sleep(0.2)  # be polite


def pass_enrich_durations(con: sqlite3.Connection, limit: int | None = None) -> None:
    """One-time backfill sweep: for every release with at least one NULL
    duration, run the MusicBrainz enrichment path *without* re-hitting
    Discogs. Skips releases that already have all durations populated
    AND skips releases that previously resolved to an MBID with a
    track-count mismatch (cached as NULL after the resolver aborts).

    Intended for existing collections that were detail-synced before
    the musicbrainz-duration-enrichment feature landed. New releases
    pick up enrichment automatically via fetch_detail in the nightly
    cron sync — see musicbrainz-duration-enrichment/idea.md.
    """
    rows = con.execute(
        "SELECT DISTINCT release_id FROM tracks "
        "WHERE duration_seconds IS NULL "
        "ORDER BY release_id",
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        log("enrich-durations: no releases with NULL durations — nothing to do")
        return

    log(
        f"enrich-durations: {len(rows)} release(s) with NULL durations — "
        f"sweeping (skips already-resolved MBID misses)",
    )
    enriched = 0
    null_before_total = con.execute(
        "SELECT COUNT(*) FROM tracks WHERE duration_seconds IS NULL",
    ).fetchone()[0]
    # Sweep-local cache of {recording_mbid: duration_seconds | None}.
    # Reissues / compilations / multi-disc box sets often repeat the same
    # recording — fetch once per sweep at most. None means "already tried,
    # was null" so we don't re-fetch.
    recording_cache: dict[str, int | None] = {}
    for i, (release_id,) in enumerate(rows, 1):
        try:
            _maybe_enrich_durations(
                con, release_id, recording_cache=recording_cache,
            )
        except Exception as e:  # noqa: BLE001 — sweep is best-effort
            log(f"  enrich-durations: release {release_id} FAILED: {e!r}")
            continue
        if i % 10 == 0 or i == len(rows):
            log(f"  enrich-durations: {i}/{len(rows)}")
        # Polite to MusicBrainz — anonymous limit is 1 req/sec; multiple
        # candidate fetches per release mean we sleep a hair longer.
        time.sleep(1.5)
        enriched += 1
    null_after_total = con.execute(
        "SELECT COUNT(*) FROM tracks WHERE duration_seconds IS NULL",
    ).fetchone()[0]
    log(
        f"enrich-durations: filled {null_before_total - null_after_total} "
        f"track duration(s) across {enriched} release(s)",
    )
