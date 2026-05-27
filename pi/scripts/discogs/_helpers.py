"""Discogs data-parsing helpers: duration, tracklist, artist/label/format joins."""
from __future__ import annotations

import json
import re
import sqlite3

import discogs_client

from ._db import log, now_iso


def _parse_discogs_duration(s: str | None) -> int | None:
    """Parse a Discogs track-duration string to seconds.

    Discogs returns durations as ``"M:SS"``, ``"MM:SS"``, or (rarely, for very
    long tracks) ``"H:MM:SS"``. Empty strings, ``None``, and anything that
    fails ``int()`` parse return ``None`` gracefully so callers can leave the
    column null.
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError as e:
        log(f"WARN: bad duration token {s!r}: {e!r}")
        return None
    if any(n < 0 for n in nums):
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


# Backwards-compat alias for any external imports.
parse_duration = _parse_discogs_duration


def iter_leaf_tracks(tracklist):  # skylos: ignore — admin sync script; CC is intrinsic to handling Discogs' nested sub_tracks tracklist format
    """Yield ``(position, title, duration_str)`` for every playable leaf track.

    Discogs multi-disc / box-set releases (Beatles Anthology, OK Computer
    OKNOTOK, Donuts, etc.) wrap real track data inside ``sub_tracks`` of an
    index/heading row whose own ``position`` is empty and ``duration`` is "".
    Recurse one level so we don't drop those tracks. Works with both
    discogs_client objects and raw dicts.
    """
    for t in tracklist or []:
        if isinstance(t, dict):
            position = t.get("position") or ""
            title = t.get("title") or ""
            duration = t.get("duration")
            subs = t.get("sub_tracks") or []
        else:
            position = getattr(t, "position", None) or ""
            title = getattr(t, "title", None) or ""
            duration = getattr(t, "duration", None)
            data = getattr(t, "data", None) or {}
            subs = data.get("sub_tracks") or []
        if subs:
            # If the parent has a real position+title, prefer the parent row
            # only when no leaf positions exist. Otherwise descend.
            yield from iter_leaf_tracks(subs)
            continue
        if not position:
            continue
        yield position, title, duration


_SUITE_LEAF_RE = re.compile(r"^([A-Z]\d+)\.\s*[IVX]+$")


def _infer_parent_position(subs) -> str:
    """Derive a parent position from a list of child sub_track rows.

    Discogs usually leaves the parent's ``position`` empty and only
    fills positions on the leaves (``A2. I``, ``A2. II``, …). The base
    prefix (``A2``) is what we need to surface on the parent so the
    reverse-lookup can derive side+position when Shazam returns the
    suite title.

    Returns the common ``X1``-style prefix when *every* child matches
    that shape and shares the same prefix; ``""`` otherwise (the
    caller should skip — no reliable position to attach).
    """
    prefixes: set[str] = set()
    for c in subs or []:
        if isinstance(c, dict):
            pos = (c.get("position") or "").strip()
        else:
            pos = (getattr(c, "position", None) or "").strip()
        m = _SUITE_LEAF_RE.match(pos)
        if m is None:
            return ""
        prefixes.add(m.group(1))
    if len(prefixes) != 1:
        return ""
    return next(iter(prefixes))


def iter_suite_parents(tracklist):
    """Yield ``(position, title, duration_str)`` for parent rows that
    wrap sub_tracks.

    Discogs models multi-movement suites (American Idiot's "Homecoming"
    → D1.I–V; "Jesus Of Suburbia" → A2.I–V) as a parent row whose
    ``sub_tracks`` carry the playable leaves. Shazam returns the parent
    title when any movement is playing, so reverse-lookup needs a row
    that ties "Homecoming" to release_id + position prefix ("D1").

    The parent itself usually has an empty ``position`` — Discogs only
    fills positions on leaves. We infer the base prefix from the
    children when every child shares one ``X1.Y`` shape. Parents that
    we can't position confidently (heterogeneous children, no
    multi-part leaves) are skipped to avoid bogus lookup hits.
    """
    for t in tracklist or []:
        if isinstance(t, dict):
            position = (t.get("position") or "").strip()
            title = t.get("title") or ""
            duration = t.get("duration")
            subs = t.get("sub_tracks") or []
        else:
            position = (getattr(t, "position", None) or "").strip()
            title = getattr(t, "title", None) or ""
            duration = getattr(t, "duration", None)
            data = getattr(t, "data", None) or {}
            subs = data.get("sub_tracks") or []
        if not subs or not title:
            continue
        if not position:
            position = _infer_parent_position(subs)
        if not position:
            continue
        yield position, title, duration
        # Recurse so nested suites (rare but possible) still surface.
        yield from iter_suite_parents(subs)


SIDE_RE = re.compile(r"^([A-Z])")


def position_to_side(position: str | None) -> str | None:
    """'A1' -> 'A', 'B-3' -> 'B', '1' -> None (CD/digital)."""
    if not position:
        return None
    m = SIDE_RE.match(position)
    return m.group(1) if m else None


def _get(obj, key, default=None):
    """Read a field from either a dict or a python3-discogs-client object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def join_artists(artists: list) -> str:
    if not artists:
        return ""
    out = []
    for i, a in enumerate(artists):
        name = _get(a, "name") or ""
        name = re.sub(r"\s*\(\d+\)\s*$", "", name)
        out.append(name)
        join = _get(a, "join")
        if join and join.strip() and i < len(artists) - 1:
            out.append(join)
    return " ".join(out).strip() or "Unknown Artist"


def join_labels(labels: list) -> tuple[str, str]:
    if not labels:
        return ("", "")
    names = []
    catnos = []
    for lab in labels:
        n = _get(lab, "name")
        c = _get(lab, "catno")
        if n:
            names.append(n)
        if c:
            catnos.append(c)
    return (", ".join(names), ", ".join(catnos))


def join_formats(formats: list) -> str:
    if not formats:
        return ""
    out = []
    for f in formats:
        name = _get(f, "name") or ""
        descs = _get(f, "descriptions") or []
        out.append(name + (" " + " ".join(descs) if descs else ""))
    return " / ".join(out).strip()


def upsert_basic(con: sqlite3.Connection, item: object) -> None:
    """item is a discogs_client CollectionItemInstance; basic_information lives in .data."""
    bi = item.data.get("basic_information", {})
    artist = join_artists(bi.get("artists") or [])
    label_names, catno = join_labels(bi.get("labels") or [])
    fmt = join_formats(bi.get("formats") or [])
    release_id = bi.get("id")
    con.execute(
        """
        INSERT INTO releases (id, artist, title, year, country, format, label, catno,
                              primary_image_url, raw_basic_json, basic_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          artist = excluded.artist,
          title = excluded.title,
          year = excluded.year,
          format = excluded.format,
          label = excluded.label,
          catno = excluded.catno,
          primary_image_url = COALESCE(excluded.primary_image_url, releases.primary_image_url),
          raw_basic_json = excluded.raw_basic_json,
          basic_synced_at = excluded.basic_synced_at
        """,
        (
            release_id,
            artist,
            bi.get("title"),
            bi.get("year") or None,
            None,  # country only on full release detail
            fmt,
            label_names,
            catno,
            bi.get("cover_image"),
            json.dumps(bi),
            now_iso(),
        ),
    )
