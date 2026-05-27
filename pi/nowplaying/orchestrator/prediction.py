"""Tracklist-aware advancement helpers — pure prediction math + the
predicted-payload assembler.
"""
from __future__ import annotations

import recognize_proto

from nowplaying.discogs import catalog as discogs_catalog


def _advance_predicted_position(
    tracks: list[dict], current: dict
) -> dict | None:
    """Advance one position forward on the same side.

    Args:
      tracks: A release's tracklist (from ``discogs_catalog.get_release``),
        a list of ``{position, side, title, duration_seconds}`` dicts in
        physical insertion order (or sorted; we use the order given).
      current: The current ``predicted_position`` dict
        ``{release_id, side, track_position, index_in_side}``.

    Returns:
      A new ``predicted_position`` dict for the next track on the same
      side, or ``None`` if ``current["track_position"]`` isn't found in
      the side's tracks OR we're already at the last position on the
      side (end-of-side — caller keeps current display, no advance).
    """
    side = current.get("side")
    if not side:
        return None
    side_tracks = [t for t in tracks if t.get("side") == side]
    if not side_tracks:
        return None
    cur_pos = current.get("track_position")
    if cur_pos is None:
        return None
    idx = next(
        (i for i, t in enumerate(side_tracks) if t.get("position") == cur_pos),
        None,
    )
    if idx is None:
        return None
    next_idx = idx + 1
    if next_idx >= len(side_tracks):
        return None
    nxt = side_tracks[next_idx]
    return {
        "release_id": current["release_id"],
        "side": side,
        "track_position": nxt["position"],
        "index_in_side": next_idx,
    }


def _build_predicted_payload(
    last_vinyl: dict, predicted: dict, source: str
) -> dict | None:
    """Build a kiosk-publish payload for a predicted track.

    Merges album-level fields from the confirmed ``last_vinyl`` lock
    (artist, album, art, label, year, tracklist) with track-level fields
    looked up from the Discogs catalog for the predicted position
    (title, side, track_position). Returns ``None`` if the release or
    matching track can't be found — caller falls back to NEEDS_ID.
    """
    release = discogs_catalog.get_release(predicted["release_id"])
    if release is None:
        return None
    target_pos = predicted["track_position"]
    track = next(
        (t for t in (release.get("tracks") or []) if t.get("position") == target_pos),
        None,
    )
    if track is None:
        return None
    payload: dict = {
        "ts": recognize_proto.now_iso(),
        "state": "PLAYING",
        "source": source,
        "match_method": "predicted",
        "predicted": True,
        "release_id": last_vinyl.get("release_id"),
        "artist": last_vinyl.get("artist"),
        "album": last_vinyl.get("album"),
        "year": last_vinyl.get("year"),
        "label": last_vinyl.get("label"),
        "catno": last_vinyl.get("catno"),
        "art_url": last_vinyl.get("art_url"),
        "tracklist": last_vinyl.get("tracklist"),
        "title": track.get("title"),
        "track_position": track.get("position"),
        "side": track.get("side"),
    }
    return payload
