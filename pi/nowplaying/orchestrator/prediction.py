"""Tracklist-aware advancement helpers — pure prediction math + the
predicted-payload assembler.
"""
from __future__ import annotations

from datetime import datetime, timezone

import recognize_proto

from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.orchestrator.pin import _confidence_for_remaining

# Match methods that represent a *confirmed* now-playing track (not a guess).
# When the payload's match_method is one of these, an attached guess is a
# passive tracklist hint, not something to prompt the user to confirm.
_CONFIRMED_MATCH_METHODS = frozenset({
    "shazam", "fingerprint", "user-identified", "user-selected",
    "sonos-didl", "sonos-polled",
})


def _elapsed_in_track_s(track_started_at_iso: str | None) -> float:
    """Seconds since the (estimated) start of the current track, from its
    ISO-8601 ``track_started_at``. Returns 0.0 on missing/unparseable input."""
    if not track_started_at_iso:
        return 0.0
    try:
        anchor = datetime.fromisoformat(track_started_at_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - anchor).total_seconds())


def enrich_guess_contract(payload: dict) -> None:
    """Stamp the guess contract — ``confidence`` / ``expires_in_s`` /
    ``confirmable`` — onto ``payload['guess']`` so the kiosk renders the guess
    without re-deriving backend state. No-op when there is no guess.
    Epic consolidate-guess-confidence-lifetime / C2.

      - ``expires_in_s``: seconds of the guessed track left (duration − elapsed),
        the single lifetime clock; ``None`` when the duration is unknown.
      - ``confidence``: track-remaining ramp (high → medium → low) via the shared
        primitive; left at the guess's source value when there is no duration.
      - ``confirmable``: True unless the now-playing track is confirmed by
        Shazam/fingerprint/user — i.e. the guess IS the now-playing guess and
        the kiosk should offer to confirm it.
    """
    guess = payload.get("guess")
    if not isinstance(guess, dict):
        return
    duration = payload.get("duration_seconds")
    if duration is None:
        guess["expires_in_s"] = None
    else:
        remaining = max(
            0.0, float(duration) - _elapsed_in_track_s(payload.get("track_started_at")),
        )
        guess["expires_in_s"] = round(remaining)
        guess["confidence"] = _confidence_for_remaining(remaining, float(duration))
    guess["confirmable"] = payload.get("match_method") not in _CONFIRMED_MATCH_METHODS


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
        # Predicted track's own duration (not last_vinyl's) so the Last.fm
        # scrobble path can apply the 50%-of-duration rule.
        "duration_seconds": track.get("duration_seconds"),
    }
    return payload
