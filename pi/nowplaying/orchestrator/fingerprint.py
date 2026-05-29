"""Build a kiosk payload from a fingerprint-cascade hit."""
from __future__ import annotations

import logging

import recognize_proto

from nowplaying.discogs import catalog as discogs_catalog

log = logging.getLogger(__name__)


def _build_fingerprint_payload(
    locked_payload: dict, top, audio_source_label: str,
) -> dict:
    """Overlay a fingerprint hit onto the locked album's payload — keeps
    album-level metadata, swaps in the matched track's position/side/
    title (via the locked tracklist lookup) and stamps a fresh ts.

    Used by the F3 confirmation path where an album lock already exists.
    For the blind-fingerprint-discovery path (no lock) use
    :func:`_build_blind_fingerprint_payload` instead.
    """
    payload = dict(locked_payload)
    payload["track_position"] = top.track_position
    payload["side"] = (
        top.track_position[:1] if top.track_position else payload.get("side")
    )
    payload["match_method"] = "fingerprint"
    payload["source"] = audio_source_label
    payload["ts"] = recognize_proto.now_iso()
    # The copied payload carries the PREVIOUS track's duration; drop it so the
    # scrobble path doesn't score this track against the wrong length, then
    # set the matched track's duration below.
    payload.pop("duration_seconds", None)
    for tr in (payload.get("tracklist") or []):
        # Tracklist entries built by the blind path use the "position" key;
        # entries built by the confirmation overlay or pin path use
        # "track_position".  Check both so this helper is correct regardless of
        # how last_vinyl was originally constructed (e.g. via blind discovery
        # followed by a confirmation hit on the same album).
        tr_pos = tr.get("track_position") or tr.get("position")
        if tr_pos == top.track_position:
            if tr.get("title"):
                payload["title"] = tr["title"]
            if tr.get("duration_seconds") is not None:
                payload["duration_seconds"] = tr["duration_seconds"]
            break
    return payload


def _build_blind_fingerprint_payload(  # skylos: ignore SKY-Q301 SKY-C304 — Why: CC 11 / 87 lines are a single-responsibility data-assembly function; branches are field-resolution guards (None-coercion, missing catalog, track not found) that can't be cleanly extracted without obscuring the payload shape
    top, audio_source_label: str,
) -> dict | None:
    """Build a full kiosk vinyl payload from a blind fingerprint hit.

    Called by the F4 blind-fingerprint-discovery path when the orchestrator
    has no locked album. Fetches full release metadata from ``discogs.sqlite``
    via :func:`discogs_catalog.get_release` and constructs a payload shaped
    like the vinyl source path (artist, album, year, label, catno,
    track_position, side, title, art_url, tracklist).

    Returns ``None`` when:
    - ``discogs_catalog.get_release`` returns ``None`` (release not in the
      local catalog snapshot) — treat as a miss and fall through to NEEDS_ID.
    - Any unexpected exception during catalog lookup (logged + swallowed).

    The caller (``_try_fingerprint_fallback``) must check for ``None`` before
    publishing. A ``None`` return causes the blind path to fall through exactly
    like a fingerprint no-match.

    Read-only: this function never writes to fp_refs or promotes audio.
    Promotion stays pin-driven only.
    """
    try:
        rel = discogs_catalog.get_release(top.release_id)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "blind fingerprint: discogs catalog lookup failed for release=%s: %r",
            top.release_id, e,
        )
        return None
    if rel is None:
        log.info(
            "blind fingerprint: release=%s not in discogs catalog — treating as no match",
            top.release_id,
        )
        return None

    # Resolve track title from the release tracklist using the matched position.
    # Key is "position" (not "track_position") to match the shape that
    # _find_pin_track and _tracklist_from_release both use, so pin-track
    # can resolve any track on this release from the inline payload.
    track_title: str | None = None
    track_duration_s: int | None = None
    tracklist: list[dict] = []
    for tr in (rel.get("tracks") or []):
        pos = tr.get("position")
        title = tr.get("title")
        side = tr.get("side") or (pos[:1] if pos else None)
        tracklist.append({
            "position": pos,
            "side": side,
            "title": title,
            "duration_seconds": tr.get("duration_seconds"),
        })
        if pos == top.track_position:
            if title:
                track_title = title
            # Top-level duration_seconds — required by the predicted-advance
            # duration guard (`_handle_unmatched_music_level`). Without it,
            # `state.last_vinyl.get("duration_seconds")` returns None and the
            # guard's `is not None` short-circuit fails open, allowing
            # cold-start predicted-advance to fire on weak matches (hits 30-89,
            # no anchor) — observed live 2026-05-19 as an italic-Leo flash on
            # the first Pitiful drop.
            track_duration_s = tr.get("duration_seconds")

    side = top.track_position[:1] if top.track_position else None
    payload: dict = {
        "source": audio_source_label,
        "match_method": "fingerprint",
        "ts": recognize_proto.now_iso(),
        "release_id": top.release_id,
        "artist": rel.get("artist"),
        "album": rel.get("title"),
        "year": rel.get("year"),
        "label": rel.get("label"),
        "catno": rel.get("catno"),
        "track_position": top.track_position,
        "side": side,
        "title": track_title,
        "duration_seconds": track_duration_s,
        "art_url": f"/art/{top.release_id}",
    }
    if tracklist:
        payload["tracklist"] = tracklist
    return payload
