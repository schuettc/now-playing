"""Recognition cascade for a captured WAV clip.

Pipeline:
    1. ShazamIO recognition (free, ~3s, requires internet). Clips must be
       <14s — Shazam's backend silently returns empty matches at >=15s
       (shazamio Issue #150).

Album/version disambiguation (which Discogs release the Shazam-named track
belongs to, when the user owns multiple copies) is handled by
`discogs.catalog.find_by_artist_title` with `preferred_release_id` stickiness
and a side-first (A1/B1) bias.

Run:
    uv run python pi/scripts/recognize_proto.py <clip.wav> [<clip2.wav> ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pi"))

from nowplaying import art_cache
from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.discogs.catalog import _normalize  # noqa: PLC2701 — shared title normalizer; same semantics on Discogs + discovered paths
from nowplaying.discovery import musicbrainz_lookup
from nowplaying.vinyl import shazam

logger = logging.getLogger(__name__)

PI_DIR = REPO_ROOT / "pi"
DATA_DIR = PI_DIR / "data"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit(d: dict) -> None:
    sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def recognize(  # skylos: ignore — prototype recognizer; production cascade lives in nowplaying/vinyl
    clip_path: Path,
    *,
    preferred_release_id: int | None = None,
    source: str = "vinyl",
) -> dict:
    """Run the cascade. Always returns a unified event dict."""
    base = {
        "ts": now_iso(),
        "clip": str(clip_path.relative_to(REPO_ROOT)) if clip_path.is_relative_to(REPO_ROOT) else str(clip_path),
        "match_method": None,
        "match_confidence": None,
        "release_id": None,
        "artist": None,
        "title": None,
        "album": None,
        "year": None,
        "label": None,
        "catno": None,
        "art_path": None,
        "tracklist": None,
    }

    # 1. ShazamIO
    try:
        sh = await shazam.identify(clip_path)
    except Exception as e:
        base["shazam_error"] = repr(e)
        sh = None
    if sh:
        base["match_method"] = "shazam"
        base["match_confidence"] = "hit"  # Shazam doesn't expose a numeric score
        # Shazam: subtitle = artist, title = track (song) name
        shazam_track_title = sh.get("title")
        shazam_artist = sh.get("subtitle")
        base["title"] = shazam_track_title
        base["artist"] = shazam_artist
        # ISRC is a strong unique track identifier; surface it so downstream
        # consumers (LLM hooks, history, future enrichment) can read it from
        # the raw recognizer output without re-querying. Source:
        # `pi/nowplaying/vinyl/shazam.py:84` populates it on the Shazam result.
        if sh.get("isrc"):
            base["isrc"] = sh.get("isrc")
        # Shazam match offset (seconds into the source track at which the
        # heartbeat clip aligned). Surfaced for the fingerprint-cascade
        # promotion path so it can space refs across the song. Optional
        # field; older shazamio responses may omit it.
        if sh.get("match_offset_s") is not None:
            base["track_position_s"] = float(sh["match_offset_s"])
        rel = discogs_catalog.find_by_artist_title(
            artist=shazam_artist or "",
            title=shazam_track_title or "",
            preferred_release_id=preferred_release_id,
        )
        if rel:
            base.update(_release_fields(rel))
            base["title"] = (
                rel.get("matched_track_clean_title")
                or rel.get("matched_track_title")
                or shazam_track_title
            )
            base["track_position"] = rel.get("matched_track_position")
            base["match_score"] = rel.get("match_score")
            # _release_fields already set base["album"] from disambiguated_album
            # (preferred) or rel["title"] (fallback). Don't overwrite with the
            # bare title here — that drops the eponymous-album disambiguator
            # (e.g. "American Football (2019)" → "American Football").
            alternates = rel.get("alternate_releases")
            if alternates:
                base["alternate_releases"] = alternates
            asyncio.create_task(
                art_cache.maybe_cache(
                    int(rel["id"]),
                    shazam_artist or "",
                    rel.get("title") or "",
                )
            )
            return base
        # Shazam-only branch: Discogs missed. Surface the Shazam-derived
        # album / cover art so the kiosk can render more than just
        # artist + title. Discogs wins when present (canonical pressing); we
        # only attach these when there's no rel above.
        if sh.get("album"):
            base["album"] = sh.get("album")
        if sh.get("art_url"):
            base["art_url"] = sh.get("art_url")
        if sh.get("albumadamid"):
            base["albumadamid"] = sh.get("albumadamid")
        # Discovered-release path: if we've previously persisted a
        # MusicBrainz tracklist for this (artist, album), attach it now
        # so the kiosk gets side-timer / tracklist-aware behavior on this
        # heartbeat. Else fire a background lookup so the next heartbeat
        # (~15s later) picks it up.
        _attach_discovered_or_schedule(base, sh, shazam_track_title or "")
        return base

    # 2. Miss
    base["match_method"] = "unmatched"
    return base


# In-flight discovery tasks keyed on (artist_norm, album_norm). Prevents
# two heartbeats ~15s apart firing duplicate MB lookups for the same album.
_in_flight_discovery: dict[tuple[str, str], asyncio.Task] = {}


def _attach_discovered_or_schedule(
    base: dict, sh: dict, shazam_track_title: str,
) -> None:
    """If discovered.sqlite has a release for (artist, album), attach
    release_mbid + tracklist to the payload, and resolve track_position +
    side by matching the Shazam title against the tracklist. Otherwise
    schedule a background MB lookup."""
    artist = sh.get("subtitle") or ""
    album = sh.get("album") or ""
    if not artist or not album:
        return
    mbid = musicbrainz_lookup.find_discovered_release_by_artist_album(
        artist, album,
    )
    if mbid:
        from nowplaying.catalog import get_release as catalog_get_release
        rel = catalog_get_release(mbid=mbid)
        if rel:
            base["release_mbid"] = mbid
            disambiguated = rel.get("disambiguated_album")
            if disambiguated:
                base["album"] = disambiguated
            tracks = rel.get("tracks") or []
            base["tracklist"] = [
                {
                    "position": t.get("position"),
                    "side": t.get("side"),
                    "title": t.get("title"),
                    "duration_seconds": t.get("duration_seconds"),
                    "clean_title": t.get("clean_title"),
                }
                for t in tracks
            ]
            _resolve_track_position(base, tracks, shazam_track_title, mbid)
            return
    _schedule_discovery(sh)


def _find_matching_track(
    tracks: list[dict], target: str,
) -> tuple[list[dict], list[dict]]:
    """Return (exact_matches, substring_matches) for `target` against
    `tracks`. Substring side hits either direction (handles parenthetical
    suffixes like 'Tighten Up (Live)' vs 'Tighten Up')."""
    exact: list[dict] = []
    fuzzy: list[dict] = []
    for t in tracks:
        norm = _normalize(t.get("title") or "")
        if not norm:
            continue
        if norm == target:
            exact.append(t)
        elif norm in target or target in norm:
            fuzzy.append(t)
    return exact, fuzzy


def _resolve_track_position(
    base: dict, tracks: list[dict], shazam_track_title: str, mbid: str,
) -> None:
    """Walk the discovered tracklist for a row whose title matches the
    Shazam track title. Fail-open (leave track_position/side unset) on no
    match or ambiguity."""
    if not shazam_track_title or not tracks:
        return
    target = _normalize(shazam_track_title)
    if not target:
        return
    exact, fuzzy = _find_matching_track(tracks, target)
    winner = exact[0] if len(exact) == 1 else (
        fuzzy[0] if not exact and len(fuzzy) == 1 else None
    )
    if winner is not None:
        base["track_position"] = winner.get("position")
        base["side"] = winner.get("side")
        return
    logger.debug(
        "discovery: title=%r not matched against tracklist of mbid=%s "
        "(exact=%d fuzzy=%d)",
        shazam_track_title, mbid, len(exact), len(fuzzy),
    )


def _schedule_discovery(shazam_result: dict) -> None:
    """Fire-and-forget background MB lookup. Try ISRC first (strongest
    signal), fall back to (artist, album). On hit, persist into
    discovered.sqlite. Negative cache on miss is handled inside
    lookup_by_artist_album. Per-(artist, album) lock prevents duplicate
    in-flight requests.
    """
    artist = shazam_result.get("subtitle") or ""
    album = shazam_result.get("album") or ""
    isrc = shazam_result.get("isrc") or ""
    key = (artist.strip().lower(), album.strip().lower())
    if not key[0] or not key[1]:
        return
    existing = _in_flight_discovery.get(key)
    if existing and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run_discovery(artist, album, isrc, key))
    _in_flight_discovery[key] = task


async def _run_discovery(
    artist: str, album: str, isrc: str,
    key: tuple[str, str],
) -> None:
    """Drive the discovery cascade: ISRC → artist/album → persist."""
    try:
        release = None
        if isrc:
            release = await musicbrainz_lookup.lookup_by_isrc(isrc)
        if release is None:
            release = await musicbrainz_lookup.lookup_by_artist_album(
                artist, album,
            )
        if release is None:
            print(  # noqa: T201 — operator log line, journalctl captures stdout
                f"discovery: no MB match for artist={artist!r} "
                f"album={album!r}",
                file=sys.stderr,
            )
            return
        await musicbrainz_lookup.persist(release)
        print(  # noqa: T201
            f"discovery: discovered release "
            f"mbid={release.get('mbid')} "
            f"artist={release.get('artist')!r} "
            f"album={release.get('album')!r}",
            file=sys.stderr,
        )
    except Exception as e:  # noqa: BLE001 — background task: log + swallow
        print(  # noqa: T201
            f"discovery: lookup raised {e!r} for artist={artist!r} "
            f"album={album!r}",
            file=sys.stderr,
        )
    finally:
        _in_flight_discovery.pop(key, None)


def _release_fields(rel: dict) -> dict:
    return {
        "release_id": rel.get("id"),
        "artist": rel.get("artist"),
        "title": rel.get("title"),
        "album": rel.get("disambiguated_album") or rel.get("title"),
        "year": rel.get("year"),
        "label": rel.get("label"),
        "catno": rel.get("catno"),
        "art_path": rel.get("art_path"),
        "tracklist": [
            {"position": t["position"], "side": t["side"], "title": t["title"],
             "duration_seconds": t["duration_seconds"],
             "clean_title": t.get("clean_title")}
            for t in (rel.get("tracks") or [])
        ],
    }


async def main_async() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("clips", nargs="+", help="WAV clip paths to recognize")
    args = p.parse_args()
    for clip_arg in args.clips:
        clip_path = Path(clip_arg).resolve()
        if not clip_path.exists():
            emit({"ts": now_iso(), "clip": str(clip_path), "error": "not_found"})
            continue
        result = await recognize(clip_path)
        emit(result)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
