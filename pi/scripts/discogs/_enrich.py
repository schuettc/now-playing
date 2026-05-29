"""MusicBrainz duration enrichment helpers for the Discogs sync."""
from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
from pathlib import Path

from ._db import PI_DIR, log

# Sentinel: two recordings normalize to the same title — callers skip the fill.
_AMBIGUOUS_TITLE = object()


def _norm_title(title: str) -> str:
    """Normalize a track title for cross-catalog matching. Lowercases,
    strips Discogs-style emphasis markers (``**Transmission 1``), strips
    leading/trailing whitespace and punctuation, collapses internal
    whitespace.

    Conservative on purpose: no fuzzy matching, no diacritic folding.
    The goal is "exact-after-cleanup" matches; anything fuzzier risks
    false positives across pressings.
    """
    if not title:
        return ""
    out = title.strip()
    # Strip leading ``**`` or ``*`` markers Discogs uses for embedded
    # subtracks (e.g. ``**Transmission 1``, ``*Intro``).
    while out.startswith("*"):
        out = out[1:].lstrip()
    # Strip trailing punctuation runs that vary across catalogs.
    out = re.sub(r"[\s.?!,;:]+$", "", out)
    # Collapse internal whitespace.
    out = re.sub(r"\s+", " ", out)
    return out.lower()


def _score_candidate(
    recordings: list[dict],
    discogs_titles_normed: set[str],
    discogs_count: int,
) -> tuple[int, int]:
    """Score one MusicBrainz candidate against the Discogs tracklist.

    Returns ``(title_matches, count_diff)`` where a higher ``title_matches``
    and lower ``count_diff`` indicate a better candidate.
    """
    mb_titles_normed = {
        _norm_title(r.get("title") or "") for r in recordings
    }
    mb_titles_normed.discard("")
    matches = len(discogs_titles_normed & mb_titles_normed)
    count_diff = abs(len(recordings) - discogs_count)
    return matches, count_diff


def _is_better_candidate(
    matches: int,
    count_diff: int,
    best_matches: int,
    best_count_diff: int,
) -> bool:
    """Return True when (matches, count_diff) beats the current best.

    Prefers more title matches; breaks ties by smaller count_diff.
    """
    return matches > best_matches or (
        matches == best_matches and count_diff < best_count_diff
    )


def _is_perfect_candidate(
    matches: int,
    count_diff: int,
    discogs_titles_normed: set[str],
) -> bool:
    """Return True when a candidate is perfect: every Discogs title matched
    AND the track count is exact.

    A reissue can match all Discogs titles while having bonus tracks the
    original doesn't — callers should keep scanning so the original
    (count_diff=0) can beat the reissue (count_diff>0) on the tiebreaker.
    Only exit early when count_diff is also zero.
    """
    return (
        matches == len(discogs_titles_normed)
        and matches > 0
        and count_diff == 0
    )


def _build_title_duration_map(
    recordings: list[dict],
) -> dict[str, int | object | None]:
    """Build {normalized_title: duration_seconds} from a MusicBrainz
    recordings list. When two recordings normalize to the same title,
    store the ``_AMBIGUOUS_TITLE`` sentinel so callers can skip.

    Null durations are stored as None (callers also skip these — title
    matched but MusicBrainz didn't know the length either).
    """
    out: dict[str, int | object | None] = {}
    for rec in recordings:
        key = _norm_title(rec.get("title") or "")
        if not key:
            continue
        dur = rec.get("duration_seconds")
        if key in out:
            # Duplicate — drop ambiguous match.
            out[key] = _AMBIGUOUS_TITLE
        else:
            out[key] = dur if dur is None else int(dur)
    return out


def _build_title_recording_mbid_map(
    recordings: list[dict],
) -> dict[str, str | object]:
    """``{normalized_title: recording_mbid}``. Mirrors
    :func:`_build_title_duration_map`'s ambiguity handling: if two
    recordings normalize to the same title, store
    ``_AMBIGUOUS_TITLE`` so callers skip the lookup.
    """
    out: dict[str, str | object] = {}
    for rec in recordings:
        key = _norm_title(rec.get("title") or "")
        if not key:
            continue
        rec_mbid = rec.get("recording_mbid")
        if not rec_mbid:
            continue
        if key in out:
            out[key] = _AMBIGUOUS_TITLE
        else:
            out[key] = rec_mbid
    return out


async def _resolve_best_matching_mbid(
    coverart_mod,
    artist: str,
    title: str,
    discogs_track_titles: list[str],
    release_id: int,
) -> str | None:
    """Search MusicBrainz for releases matching artist+title, then walk
    the candidates and return the MBID whose tracklist best matches
    the Discogs side. Scoring favors **title matches** over raw track
    counts so structural mismatches (Discogs subtracks vs MusicBrainz
    main tracks, multi-part suite splits, box-set boundaries) still
    resolve correctly.

    Returns None when no candidate has any title overlap with the
    Discogs tracklist — that's the only signal that survives across
    Discogs↔MusicBrainz structural differences. A wildly-wrong release
    (e.g. completely different album) would produce zero overlap.

    Why this exists: MusicBrainz often catalogs multiple releases of the
    same album (original pressing, reissue with bonus tracks, regional
    variants, anniversary editions). Picking by track-count alone misses
    structural-mismatch cases like Pink Floyd's *Wish You Were Here*
    (Discogs splits Shine On parts; MusicBrainz keeps it as 5 tracks)
    or DJ Shadow's *Endtroducing* (Discogs has Transmission subtracks;
    MusicBrainz doesn't). Title-overlap scoring picks the right MBID
    in all these cases.

    Bounded by ``search_release_candidates``'s ``limit=6`` so this makes
    at most ~6 additional MusicBrainz API calls — only on first resolve,
    after which the MBID is cached on the release row.
    """
    candidates = await coverart_mod.search_release_candidates(artist, title)
    if not candidates:
        log(
            f"musicbrainz-enrich: release={release_id} no MusicBrainz "
            f"match for artist={artist!r} title={title!r}",
        )
        return None

    # Normalize the Discogs titles once so candidate scoring is fast.
    discogs_titles_normed = {
        _norm_title(t) for t in discogs_track_titles if t
    }
    discogs_count = len(discogs_track_titles)

    best_mbid: str | None = None
    best_matches: int = -1
    best_count_diff: int = 0
    for mbid, _rg in candidates:  # skylos: ignore SKY-U401 — Why: _rg is the release-group name from the search result tuple; intentionally discarded — only the mbid is needed for the recording fetch
        recordings = await coverart_mod.fetch_release_recordings(mbid)
        if recordings is None:
            continue
        matches, count_diff = _score_candidate(
            recordings, discogs_titles_normed, discogs_count,
        )
        if _is_better_candidate(matches, count_diff, best_matches, best_count_diff):
            best_mbid = mbid
            best_matches = matches
            best_count_diff = count_diff
        if _is_perfect_candidate(matches, count_diff, discogs_titles_normed):
            break

    if best_mbid is None or best_matches <= 0:
        log(
            f"musicbrainz-enrich: release={release_id} no candidate "
            f"with title overlap (from {len(candidates)} MB match(es)); "
            f"aborting to prevent cross-pressing contamination",
        )
        return None
    log(
        f"musicbrainz-enrich: release={release_id} resolved mbid={best_mbid} "
        f"({best_matches}/{len(discogs_titles_normed)} title matches, "
        f"count diff={best_count_diff})",
    )
    return best_mbid


async def _resolve_mbid_if_needed(
    coverart_mod,
    con: sqlite3.Connection,
    release_id: int,
    artist: str,
    title: str,
    mbid: str | None,
) -> str | None:
    """Return the cached MBID or resolve + cache it via MusicBrainz search.

    Returns None when resolution fails (no match, empty artist/title).
    """
    if mbid:
        return mbid
    if not artist or not title:
        log(
            f"musicbrainz-enrich: release={release_id} missing "
            f"artist/title; skipping",
        )
        return None
    discogs_titles = [
        row[0] or ""
        for row in con.execute(
            "SELECT title FROM tracks WHERE release_id = ? ORDER BY rowid",
            (release_id,),
        ).fetchall()
    ]
    resolved = await _resolve_best_matching_mbid(
        coverart_mod, artist, title, discogs_titles, release_id,
    )
    if resolved is None:
        return None
    con.execute(
        "UPDATE releases SET musicbrainz_mbid = ? WHERE id = ?",
        (resolved, release_id),
    )
    return resolved


def _warn_if_large_count_diff(
    release_id: int,
    mbid: str,
    discogs_tracks: list,
    recordings: list[dict],
) -> None:
    """Log a soft warning when Discogs and MusicBrainz track counts diverge
    significantly. Title-matching makes structural mismatches safe, but the
    WARN keeps telemetry on potentially-wrong MBID matches.
    """
    if not discogs_tracks or not recordings:
        return
    count_ratio = (
        min(len(discogs_tracks), len(recordings))
        / max(len(discogs_tracks), len(recordings))
    )
    if count_ratio < 0.5:
        log(
            f"musicbrainz-enrich: release={release_id} mbid={mbid} "
            f"large tracklist count diff (discogs={len(discogs_tracks)} "
            f"musicbrainz={len(recordings)}); proceeding with "
            f"title-match — verify MBID is correct if updated == 0",
        )


def _fill_null_durations_by_title(
    con: sqlite3.Connection,
    release_id: int,
    discogs_tracks: list,
    mb_by_title: dict,
) -> int:
    """Update NULL duration rows using the title-keyed MusicBrainz map.

    Returns the count of rows updated.
    """
    updated = 0
    for d_pos, d_title, d_dur, d_clean in discogs_tracks:
        if d_dur is not None:
            continue  # never overwrite an existing Discogs duration
        mb_dur = mb_by_title.get(_norm_title(d_clean or d_title or ""))
        if mb_dur is None or mb_dur is _AMBIGUOUS_TITLE:
            continue
        cur = con.execute(
            "UPDATE tracks SET duration_seconds = ? "
            "WHERE release_id = ? AND position = ? "
            "AND duration_seconds IS NULL",
            (int(mb_dur), release_id, d_pos),
        )
        if cur.rowcount:
            updated += cur.rowcount
    return updated


async def _apply_recording_level_fallback(
    con: sqlite3.Connection,
    *,
    release_id: int,
    mb_by_title_rec: dict[str, str | object],
    recording_cache: dict[str, int | None] | None,
) -> int:
    """For any Discogs track still NULL after the title-match fill, look
    up the canonical recording entity's length. Uses the in-sweep cache
    when provided so duplicate recording_mbids fetch once per sweep.

    Polite-sleeps ~1.1s between actual HTTP calls (MB anonymous limit is
    1 req/sec). Cache lookups skip the sleep.
    """
    sys.path.insert(0, str(PI_DIR))
    from nowplaying import coverart  # noqa: E402

    remaining = con.execute(
        "SELECT position, title, clean_title FROM tracks "
        "WHERE release_id = ? AND duration_seconds IS NULL "
        "ORDER BY rowid",
        (release_id,),
    ).fetchall()
    if not remaining:
        return 0

    cache: dict[str, int | None] = (
        recording_cache if recording_cache is not None else {}
    )
    updated = 0
    for d_pos, d_title, d_clean in remaining:
        rec_mbid = mb_by_title_rec.get(_norm_title(d_clean or d_title or ""))
        if not rec_mbid or rec_mbid is _AMBIGUOUS_TITLE:
            continue
        # Narrow type for the static checker.
        assert isinstance(rec_mbid, str)  # nosec
        if rec_mbid in cache:
            dur = cache[rec_mbid]
        else:
            dur = await coverart.fetch_recording_duration(rec_mbid)
            cache[rec_mbid] = dur
            # Polite to MusicBrainz — anonymous limit is 1 req/sec.
            await asyncio.sleep(1.1)
        if dur is None:
            continue
        cur = con.execute(
            "UPDATE tracks SET duration_seconds = ? "
            "WHERE release_id = ? AND position = ? "
            "AND duration_seconds IS NULL",
            (int(dur), release_id, d_pos),
        )
        if cur.rowcount:
            log(
                f"musicbrainz-enrich: release={release_id} recording-level "
                f"fill pos={d_pos} mbid={rec_mbid} -> {int(dur)}s",
            )
            updated += cur.rowcount
    return updated


async def _enrich_durations_from_musicbrainz_async(
    con: sqlite3.Connection,
    release_id: int,
    *,
    recording_cache: dict[str, int | None] | None = None,
) -> int:
    """Async core of the enrichment path. See _maybe_enrich_durations.

    Reads artist/title from the already-populated `releases` row (basic
    sync runs before detail sync, so these are present). Uses the
    orchestrator's MusicBrainz client (same User-Agent, negative-cache,
    rate-limit handling).
    """
    sys.path.insert(0, str(PI_DIR))
    from nowplaying import coverart  # noqa: E402

    row = con.execute(
        "SELECT artist, title, musicbrainz_mbid FROM releases WHERE id = ?",
        (release_id,),
    ).fetchone()
    if row is None:
        return 0
    artist, title, mbid = row[0] or "", row[1] or "", row[2]

    mbid = await _resolve_mbid_if_needed(coverart, con, release_id, artist, title, mbid)
    if mbid is None:
        return 0

    recordings = await coverart.fetch_release_recordings(mbid)
    if not recordings:
        log(
            f"musicbrainz-enrich: release={release_id} mbid={mbid} "
            f"{'fetch failed' if recordings is None else 'returned empty recording list'}",
        )
        return 0

    discogs_tracks = con.execute(
        "SELECT position, title, duration_seconds, clean_title FROM tracks "
        "WHERE release_id = ? ORDER BY rowid",
        (release_id,),
    ).fetchall()

    _warn_if_large_count_diff(release_id, mbid, discogs_tracks, recordings)

    # Title-keyed match: only tracks whose normalized title uniquely matches
    # a MusicBrainz recording get filled. See _build_title_duration_map.
    mb_by_title = _build_title_duration_map(recordings)
    updated = _fill_null_durations_by_title(con, release_id, discogs_tracks, mb_by_title)

    # Recording-level fallback for tracks still NULL after title-match fill.
    mb_by_title_rec = _build_title_recording_mbid_map(recordings)
    updated += await _apply_recording_level_fallback(
        con,
        release_id=release_id,
        mb_by_title_rec=mb_by_title_rec,
        recording_cache=recording_cache,
    )
    return updated


async def clean_release_titles(con: sqlite3.Connection, release_id: int, llm=None) -> int:
    """Populate tracks.clean_title / clean_title_source for one release.
    Only fills rows where clean_title IS NULL. Returns rows updated."""
    from nowplaying.titleclean import clean_titles

    rows = con.execute(
        "SELECT position, title FROM tracks "
        "WHERE release_id = ? AND clean_title IS NULL",
        (release_id,),
    ).fetchall()
    if not rows:
        return 0
    cleaned = await clean_titles([(t or "") for _pos, t in rows], llm)
    updated = 0
    for position, title in rows:
        clean, source = cleaned[title or ""]
        cur = con.execute(
            "UPDATE tracks SET clean_title = ?, clean_title_source = ? "
            "WHERE release_id = ? AND position = ?",
            (clean, source, release_id, position),
        )
        updated += cur.rowcount
    con.commit()
    return updated


def _maybe_enrich_durations(
    con: sqlite3.Connection,
    release_id: int,
    recording_cache: dict[str, int | None] | None = None,
) -> None:
    """Fallback: when Discogs left any track with a NULL duration, look up
    the matching release on MusicBrainz and fill in the gaps. Only updates
    NULL rows — pre-existing Discogs durations are preserved.

    Caches the resolved MBID on ``releases.musicbrainz_mbid`` so subsequent
    syncs skip the search step. Guarded by a tracklist-count comparison
    so a cross-pressing MusicBrainz match can't contaminate our data.

    All failure modes (no MBID resolved, HTTP error, tracklist mismatch,
    parse failure) log and return without raising — the sync flow stays
    robust.
    """
    null_count = con.execute(
        "SELECT COUNT(*) FROM tracks WHERE release_id = ? AND duration_seconds IS NULL",
        (release_id,),
    ).fetchone()[0]
    if null_count == 0:
        return

    log(
        f"musicbrainz-enrich: release={release_id} has {null_count} "
        f"NULL-duration track(s) — attempting MusicBrainz fallback",
    )

    try:
        updated = asyncio.run(
            _enrich_durations_from_musicbrainz_async(
                con, release_id,
                recording_cache=recording_cache,
            ),
        )
    except Exception as e:  # noqa: BLE001 — enrichment is best-effort
        log(f"musicbrainz-enrich: release={release_id} failed: {e!r}")
        return

    log(
        f"musicbrainz-enrich: release={release_id} updated "
        f"{updated} track(s)",
    )
