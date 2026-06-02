"""Catalog dispatch — Discogs or discovered (MusicBrainz-sourced).

Thin wrapper over :mod:`nowplaying.discogs.catalog` and
:mod:`nowplaying.discovery`. Callers pass ``release_id`` (Discogs) OR
``mbid`` (discovered) and the dispatcher routes to the right backing
store, returning a release dict in a shape compatible with both paths.

Callers that always hold a Discogs ``release_id`` can keep using
:mod:`nowplaying.discogs.catalog` directly; this module exists so the
recognize + orchestrator paths don't have to branch on ID type.
"""
from __future__ import annotations

import logging
from typing import Optional

from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.discovery import open_ro as _discovered_open_ro

log = logging.getLogger("nowplaying.catalog")


def get_release(
    release_id: int | None = None,
    mbid: str | None = None,
) -> Optional[dict]:
    """Return a release dict for either ID. Discogs path returns the
    existing schema; discovered path returns ``{mbid, artist, title,
    year, art_url, tracks: [...]}`` keyed the same way callers expect.

    When both IDs are passed, Discogs wins (canonical pressing).
    """
    if release_id is not None:
        return discogs_catalog.get_release(int(release_id))
    if mbid:
        return _get_discovered_release(mbid)
    return None


def first_position_per_side(
    release_id: int | None = None,
    mbid: str | None = None,
) -> dict[str, str]:
    """Return ``{side: first-track-position}`` for either backend."""
    if release_id is not None:
        return discogs_catalog.first_position_per_side(int(release_id))
    if mbid:
        return _discovered_first_position_per_side(mbid)
    return {}


def rid_to_album(
    release_id: int | None = None,
    mbid: str | None = None,
) -> Optional[tuple[str, str]]:
    """Return ``(artist, title)`` for either backend, or None."""
    if release_id is not None:
        return discogs_catalog.rid_to_album(int(release_id))
    if mbid:
        rel = _get_discovered_release(mbid)
        if not rel:
            return None
        artist = (rel.get("artist") or "").strip()
        title = (rel.get("title") or "").strip()
        if not artist or not title:
            return None
        return artist, title
    return None


# ── Discovered-backed implementations ───────────────────────────────────


def _get_discovered_release(mbid: str) -> Optional[dict]:
    try:
        with _discovered_open_ro() as con:
            rel_row = con.execute(
                "SELECT * FROM releases WHERE mbid = ?", (mbid,),
            ).fetchone()
            if not rel_row:
                return None
            track_rows = con.execute(
                "SELECT position, side, title, duration_seconds, clean_title "
                "FROM tracks WHERE mbid = ? ORDER BY rowid",
                (mbid,),
            ).fetchall()
            out = dict(rel_row)
            out["tracks"] = [dict(t) for t in track_rows]
            disambiguated = _discovered_disambiguated_album(con, out, mbid)
            if disambiguated:
                out["disambiguated_album"] = disambiguated
            return out
    except Exception as e:  # noqa: BLE001 — DB-missing on first boot tolerated
        log.debug("catalog: discovered lookup failed for %s: %r", mbid, e)
        return None


def _discovered_disambiguated_album(
    con, rel: dict, mbid: str,
) -> Optional[str]:
    """Return ``"{title} ({year})"`` when ≥2 discovered releases by the
    same artist share this (case-insensitive) title and at least one
    sibling has a different year. Falls back to a short-mbid suffix
    when all siblings share the year (discovered table has no catno).
    Returns None when year is null and no usable fallback exists.
    """
    artist = (rel.get("artist") or "").strip().lower()
    title_raw = rel.get("title") or ""
    title_lower = title_raw.strip().lower()
    if not artist or not title_lower:
        return None
    try:
        siblings = con.execute(
            "SELECT mbid, year FROM releases "
            "WHERE LOWER(artist) = ? AND LOWER(title) = ? AND mbid != ?",
            (artist, title_lower, mbid),
        ).fetchall()
    except Exception:  # noqa: BLE001 — DB shape on first boot tolerated
        return None
    if not siblings:
        return None
    year = rel.get("year")
    if year is not None and any(
        s["year"] is not None and s["year"] != year for s in siblings
    ):
        return f"{title_raw} ({year})"
    # All siblings share this year (or null). Fall back to an mbid
    # short-prefix suffix — the discovered.sqlite schema has no catno.
    short = (mbid or "").split("-", 1)[0]
    if short:
        return f"{title_raw} ({short})"
    return None


def _discovered_first_position_per_side(mbid: str) -> dict[str, str]:
    try:
        with _discovered_open_ro() as con:
            rows = con.execute(
                "SELECT side, position FROM tracks "
                "WHERE mbid = ? AND side IS NOT NULL ORDER BY rowid",
                (mbid,),
            ).fetchall()
    except Exception:  # noqa: BLE001 — DB-missing on first boot tolerated
        return {}
    firsts: dict[str, str] = {}
    for row in rows:
        side = row["side"]
        position = row["position"]
        if not side or not position:
            continue
        firsts.setdefault(side, position)
    return firsts


__all__ = ["get_release", "first_position_per_side", "rid_to_album"]
