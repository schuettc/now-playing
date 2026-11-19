"""MusicBrainz lookup + persistence for the discovered-release path.

Two entry points used by the recognize cascade:

* :func:`lookup_by_isrc` — strongest signal when Shazam returned one;
  walks recording → releases and takes the first non-bootleg release
  titled like the Shazam album.
* :func:`lookup_by_artist_album` — fallback when no ISRC. Wraps the
  existing :func:`coverart.fetch_release_mbid` + track-count-aware
  resolver and :func:`coverart.fetch_release_recordings`.

Both return ``{"mbid", "artist", "album", "year", "tracks"}`` or ``None``.

:func:`persist` writes the release + tracks into ``discovered.sqlite``
idempotently, with the Shazam (artist, album) that led to it, which is
what :func:`find_discovered_release_by_artist_album` looks up.
"""
from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from collections import Counter
from typing import Optional

import aiohttp

from nowplaying import art_cache, coverart
from nowplaying.coverart import Unreachable
from nowplaying.discovery._normalize import (
    albums_match,
    normalize_album,
    normalize_artist,
)
from nowplaying.discovery.schema import (
    DISCOVERED_DB_PATH,
    open_ro,
    open_rw,
)
from nowplaying.titleclean import clean_title as _clean_title

log = logging.getLogger("nowplaying.discovery")

USER_AGENT = coverart.USER_AGENT
_NEGATIVE_TTL_S = 7 * 24 * 3600

# Sides A..Z by medium ordinal; single-medium vinyl releases come out
# as A1, A2, ... — downstream consumers treat the tracklist opaquely,
# so this is good enough for side-timer / BEST GUESS rendering.
_SIDE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _norm(s: str) -> str:
    return (s or "").strip().lower()


# ── Negative cache (DB-backed) ─────────────────────────────────────────


def _negative_cached(artist: str, album: str) -> bool:
    """Check the discovered.sqlite negative_lookups table. Returns False
    if the table is missing (first boot before init_db) or the row is
    expired."""
    key = (_norm(artist), _norm(album))
    if not key[0] or not key[1]:
        return False
    try:
        with open_ro() as con:
            row = con.execute(
                "SELECT stamped_at FROM negative_lookups "
                "WHERE artist_norm = ? AND album_norm = ?",
                key,
            ).fetchone()
    except Exception:  # noqa: BLE001 — first-boot OperationalError / file-missing
        return False
    if not row:
        return False
    stamped_at = int(row["stamped_at"])
    if time.time() - stamped_at > _NEGATIVE_TTL_S:
        return False
    return True


def _mark_negative(artist: str, album: str) -> None:
    """Stamp a (artist, album) miss in the discovered.sqlite negative cache."""
    key = (_norm(artist), _norm(album))
    if not key[0] or not key[1]:
        return
    try:
        with open_rw() as con:
            con.execute(
                "INSERT OR REPLACE INTO negative_lookups "
                "(artist_norm, album_norm, stamped_at) VALUES (?, ?, ?)",
                (key[0], key[1], int(time.time())),
            )
            con.commit()
    except Exception as e:  # noqa: BLE001 — best-effort cache write
        log.debug("discovery: negative_cache write failed: %r", e)


# ── Internal: walk MB media → tracks list with synthesized side ────────


def _walk_media_to_tracks(media: list[dict]) -> list[dict]:
    """Flatten MusicBrainz ``media`` array into our internal tracks shape.

    Each medium → one side letter (A, B, C...). Each track within →
    ``{"position": "A1", "side": "A", "title": ..., "duration_seconds": int|None}``.

    MB occasionally returns >26 media (box sets); fall back to ``Z`` for
    overflow so positions stay parseable.
    """
    out: list[dict] = []
    for medium_idx, medium in enumerate(media or []):
        side = (
            _SIDE_LETTERS[medium_idx]
            if medium_idx < len(_SIDE_LETTERS) else "Z"
        )
        tracks = medium.get("tracks") or []
        for track_idx, track in enumerate(tracks):
            length_ms = track.get("length")
            duration_s: int | None
            if length_ms is None:
                duration_s = None
            else:
                try:
                    duration_s = int(round(int(length_ms) / 1000))
                except (TypeError, ValueError):
                    duration_s = None
            out.append({
                "position": f"{side}{track_idx + 1}",
                "side": side,
                "title": track.get("title") or "",
                "duration_seconds": duration_s,
            })
    return out


# ── Public lookups ─────────────────────────────────────────────────────


def _pick_recording_length(recordings: list[dict]) -> int | None:
    """Best-effort duration (seconds) from MB recording-search results.
    Restrict to recordings that have a length and the max MB score; among
    those pick the most common length, tie-break shortest. None if no
    candidate has a length."""
    cand = [r for r in recordings if r.get("length")]
    if not cand:
        return None
    top = max(int(r.get("score") or 0) for r in cand)
    lengths = [int(r["length"]) for r in cand if int(r.get("score") or 0) == top]
    counts = Counter(lengths)
    best_ms = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return round(best_ms / 1000)


async def recording_length_by_isrc(isrc: str, *, timeout_s: float = 15.0) -> int | None:
    """Look up the recognized recording's length (seconds) by ISRC via
    MusicBrainz recording search. Returns None on no ISRC, no match or no
    length; raises ``Unreachable`` on a network/parse error or non-200.
    Distinct from `lookup_by_isrc` (which walks to a release); this only
    reads the recording length."""
    if not isrc:
        return None
    query = urllib.parse.quote(f"isrc:{isrc}")
    url = (
        f"https://musicbrainz.org/ws/2/recording/?query={query}"
        f"&fmt=json&limit=10"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with art_cache.musicbrainz_slot():
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from hardcoded musicbrainz.org template; only ISRC interpolated via urllib.quote
                    if resp.status != 200:
                        raise Unreachable(f"status={resp.status}")
                    data = await resp.json()
    except Unreachable:
        raise
    except Exception as e:  # noqa: BLE001 — network/parse: not an answer
        raise Unreachable(repr(e)) from e
    return _pick_recording_length(data.get("recordings") or [])


async def lookup_by_isrc(
    isrc: str,
    album: str,
    *,
    timeout_s: float = 15.0,
) -> Optional[dict]:
    """Look up a release by ISRC. Walks ``recording?query=isrc:<isrc>``
    and picks the first non-bootleg release of those recordings that is
    titled like ``album`` (the Shazam album). Fetches that release's full
    media/tracks.

    Returns ``{"mbid", "artist", "album", "year", "tracks"}`` or None.
    """
    if not isrc:
        return None
    query = urllib.parse.quote(f"isrc:{isrc}")
    url = (
        f"https://musicbrainz.org/ws/2/recording/?query={query}"
        f"&fmt=json&limit=5"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with art_cache.musicbrainz_slot():
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from hardcoded musicbrainz.org template; only ISRC interpolated through urllib.quote
                    if resp.status != 200:
                        log.info(
                            "discovery: isrc=%s lookup status=%d",
                            isrc, resp.status,
                        )
                        return None
                    data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.info("discovery: isrc=%s lookup failed: %r", isrc, e)
        return None

    release_mbid = _pick_release_from_recording_search(data, album)
    if not release_mbid:
        return None
    try:
        return await _fetch_release_full(release_mbid, timeout_s=timeout_s)
    except Unreachable:
        return None


def _pick_release_from_recording_search(
    data: dict, album: str,
) -> Optional[str]:
    """Walk a ``recording?query=isrc:...`` response and pull the first
    non-bootleg release MBID titled like ``album``. A recording's other
    releases (the single, a compilation) carry a different tracklist.
    Returns None when no such release exists.
    """
    recordings = data.get("recordings") or []
    for rec in recordings:
        for rel in rec.get("releases") or []:
            status = (rel.get("status") or "").lower()
            if status == "bootleg":
                continue
            if not albums_match(rel.get("title") or "", album):
                continue
            rid = rel.get("id")
            if rid:
                return rid
    return None


async def _fetch_release_full(
    release_mbid: str,
    *,
    timeout_s: float,
) -> Optional[dict]:
    """Fetch a MB release with media/tracks + artist-credit and synthesize
    the discovery-release dict shape. None when MusicBrainz has no such
    release or it has no tracks; raises ``Unreachable`` otherwise.
    """
    url = (
        f"https://musicbrainz.org/ws/2/release/{release_mbid}"
        "?inc=recordings+artist-credits&fmt=json"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with art_cache.musicbrainz_slot():
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:  # skylos: ignore SKY-D216 — url built from hardcoded musicbrainz.org template; only MBID interpolated
                    if resp.status != 200:
                        log.info(
                            "discovery: release %s fetch status=%d",
                            release_mbid, resp.status,
                        )
                        if resp.status == 404:
                            return None
                        raise Unreachable(f"status={resp.status}")
                    data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.info(
            "discovery: release %s fetch failed: %r", release_mbid, e,
        )
        raise Unreachable(repr(e)) from e

    artist = _extract_artist_credit(data)
    album = data.get("title") or ""
    year = _extract_year(data.get("date"))
    tracks = _walk_media_to_tracks(data.get("media") or [])
    if not tracks:
        return None
    return {
        "mbid": release_mbid,
        "artist": artist,
        "album": album,
        "year": year,
        "tracks": tracks,
    }


def _extract_artist_credit(data: dict) -> str:
    credits = data.get("artist-credit") or []
    parts: list[str] = []
    for cred in credits:
        name = cred.get("name") or (cred.get("artist") or {}).get("name") or ""
        joinphrase = cred.get("joinphrase") or ""
        parts.append(name + joinphrase)
    return "".join(parts).strip()


def _extract_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    head = date_str.split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


async def lookup_by_artist_album(
    artist: str,
    album: str,
    *,
    track_count_hint: int | None = None,
    timeout_s: float = 15.0,
) -> Optional[dict]:
    """Look up by ``(artist, album)``. Resolves the best MBID via either
    the existing track-count-aware resolver (when ``track_count_hint`` is
    set) or the simpler ``fetch_release_mbid`` first-match path, then
    fetches the full release for tracklist + durations.

    Returns the same shape as :func:`lookup_by_isrc` or None. A miss is
    negative-cached for ``_NEGATIVE_TTL_S``; a search or release fetch
    that could not reach MusicBrainz is not.
    """
    if not artist or not album:
        return None
    if _negative_cached(artist, album):
        log.debug(
            "discovery: negative-cache hit for artist=%r album=%r",
            artist, album,
        )
        return None

    try:
        release_mbid = await _resolve_artist_album_mbid(
            artist, album, track_count_hint=track_count_hint,
            timeout_s=timeout_s,
        )
    except Unreachable as e:
        log.info(
            "discovery: MusicBrainz unreachable for artist=%r album=%r: %r",
            artist, album, e,
        )
        return None
    if not release_mbid:
        _mark_negative(artist, album)
        return None

    try:
        out = await _fetch_release_full(release_mbid, timeout_s=timeout_s)
    except Unreachable:
        return None
    if out is None:
        _mark_negative(artist, album)
    return out


async def _resolve_artist_album_mbid(
    artist: str,
    album: str,
    *,
    track_count_hint: int | None,
    timeout_s: float,
) -> Optional[str]:
    """Resolve the best MBID for (artist, album). When a track-count
    hint is provided, walk up to 6 candidates and pick the one whose
    track count is closest. Without a hint, just return the first match.
    None means MusicBrainz has no such release; raises ``Unreachable``
    when the search, or every candidate's tracklist fetch, got no answer.
    """
    if track_count_hint is None or track_count_hint <= 0:
        hit = await coverart.fetch_release_mbid(
            artist, album, timeout_s=timeout_s,
        )
        return hit[0] if hit else None

    candidates = await coverart.search_release_candidates(
        artist, album, timeout_s=timeout_s,
    )
    if not candidates:
        return None
    best_mbid: Optional[str] = None
    best_diff = -1
    unreachable = 0
    for mbid, _rg in candidates:  # skylos: ignore SKY-U401 — _rg (release-group MBID) is intentionally unused; only mbid drives the recording fetch
        recordings = await coverart.fetch_release_recordings(
            mbid, timeout_s=timeout_s,
        )
        if recordings is None:
            unreachable += 1
            continue
        diff = abs(len(recordings) - track_count_hint)
        if best_mbid is None or diff < best_diff:
            best_mbid = mbid
            best_diff = diff
            if diff == 0:
                break
    if best_mbid is None and unreachable:
        raise Unreachable(f"{unreachable} candidate tracklists not fetched")
    return best_mbid


# ── Persistence ────────────────────────────────────────────────────────


async def persist(
    release: dict, *, lookup_key: tuple[str, str] | None = None,
) -> None:
    """Write a release + tracks into discovered.sqlite. Idempotent:
    re-running with the same MBID updates the release row and rewrites
    its tracks, keeping a stored duration where the new tracklist has
    none.

    ``lookup_key`` is the Shazam ``(artist, album)`` that led to this
    release; it is stored so the same Shazam strings find the release
    again. When the album is already stored under another MBID (same
    artist, title and year), the key points at that row and no second
    row is written.
    """
    mbid = release.get("mbid")
    if not mbid:
        return
    tracks = release.get("tracks") or []
    # Clean all track titles before handing off to the sync writer.
    cleaned: list[tuple[str, str]] = []
    for t in tracks:
        raw = t.get("title") or ""
        cleaned.append(_clean_title(raw))
    await asyncio.to_thread(_persist_sync, release, cleaned, lookup_key)


def _stored_mbid_of_same_album(con, release: dict) -> Optional[str]:
    """MBID of a different stored release with this one's artist, title
    and year, or None."""
    row = con.execute(
        "SELECT mbid FROM releases "
        "WHERE mbid != ? AND LOWER(artist) = ? AND normalized_album = ? "
        "AND year IS ? ORDER BY discovered_at, mbid",
        (
            release["mbid"],
            normalize_artist(release.get("artist") or ""),
            normalize_album(release.get("album") or ""),
            release.get("year"),
        ),
    ).fetchone()
    return row["mbid"] if row else None


def _write_release(con, release: dict, cleaned: list[tuple[str, str]]) -> None:
    mbid = release["mbid"]
    title = release.get("album") or ""
    con.execute(
        "INSERT INTO releases "
        "(mbid, artist, title, year, art_url, "
        "discogs_release_id, discovered_at, normalized_album) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(mbid) DO UPDATE SET "
        "artist = excluded.artist, title = excluded.title, "
        "year = excluded.year, "
        "art_url = COALESCE(excluded.art_url, releases.art_url), "
        "discogs_release_id = COALESCE("
        "excluded.discogs_release_id, releases.discogs_release_id), "
        "normalized_album = excluded.normalized_album",
        (mbid, release.get("artist") or "", title, release.get("year"),
         release.get("art_url"), release.get("discogs_release_id"),
         int(time.time()), normalize_album(title)),
    )
    stored_durations = {
        row["position"]: row["duration_seconds"]
        for row in con.execute(
            "SELECT position, duration_seconds FROM tracks WHERE mbid = ?",
            (mbid,),
        )
    }
    con.execute("DELETE FROM tracks WHERE mbid = ?", (mbid,))
    for t, (clean, source) in zip(release.get("tracks") or [], cleaned):
        position = t.get("position") or ""
        duration = t.get("duration_seconds")
        if duration is None:
            duration = stored_durations.get(position)
        con.execute(
            "INSERT INTO tracks "
            "(mbid, position, side, title, duration_seconds, "
            "clean_title, clean_title_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mbid, position, t.get("side"), t.get("title") or "",
             duration, clean, source),
        )


def _persist_sync(
    release: dict,
    cleaned: list[tuple[str, str]],
    lookup_key: tuple[str, str] | None,
) -> None:
    with open_rw() as con:
        con.execute("BEGIN")
        try:
            target = release["mbid"]
            stored = (
                _stored_mbid_of_same_album(con, release) if lookup_key else None
            )
            if stored:
                target = stored
            else:
                _write_release(con, release, cleaned)
            if lookup_key:
                con.execute(
                    "INSERT OR REPLACE INTO release_aliases "
                    "(artist_norm, album_norm, mbid) VALUES (?, ?, ?)",
                    (normalize_artist(lookup_key[0]),
                     normalize_album(lookup_key[1]), target),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise


# ── Lookup-by-(artist, album) into discovered.sqlite ───────────────────


def find_discovered_release_by_artist_album(
    artist: str, album: str,
) -> Optional[str]:
    """Synchronous lookup: is there a discovered release for this
    (artist, album)? Returns the MBID or None. Used by the recognize
    cascade to attach tracklist on a Shazam hit when MB was already
    persisted from a prior heartbeat.

    Matches first on the Shazam key stored by :func:`persist`, then on
    the release's own artist + ``normalized_album`` so Shazam's
    edition-suffixed strings (``"Brothers (Deluxe Remastered Anniversary
    Edition)"``) resolve to MB's canonical row (``"Brothers"``). Falls
    back to legacy exact LOWER(title) match for unmigrated rows
    (shouldn't trip after init_db backfill, but defensive).
    """
    if not artist or not album:
        return None
    artist_norm = normalize_artist(artist)
    album_norm = normalize_album(album)
    try:
        with open_ro() as con:
            row = con.execute(
                "SELECT a.mbid FROM release_aliases a "
                "JOIN releases r ON r.mbid = a.mbid "
                "WHERE a.artist_norm = ? AND a.album_norm = ?",
                (artist_norm, album_norm),
            ).fetchone()
            if row:
                return row["mbid"]
            row = con.execute(
                "SELECT mbid FROM releases "
                "WHERE LOWER(artist) = ? AND normalized_album = ?",
                (artist_norm, album_norm),
            ).fetchone()
            if row:
                return row["mbid"]
            # Legacy fallback for rows missing normalized_album.
            row = con.execute(
                "SELECT mbid FROM releases "
                "WHERE LOWER(artist) = ? AND LOWER(title) = ?",
                (artist_norm, album.strip().lower()),
            ).fetchone()
    except Exception:  # noqa: BLE001 — first-boot OperationalError tolerated
        return None
    return row["mbid"] if row else None


__all__ = [
    "DISCOVERED_DB_PATH",
    "lookup_by_isrc",
    "lookup_by_artist_album",
    "recording_length_by_isrc",
    "Unreachable",
    "persist",
    "find_discovered_release_by_artist_album",
]
