"""PublishEnrichmentMixin — payload enrichment and publish helpers.

Contains: _attach_pending_guess, _adopt_heuristic_anchor, _anchor_and_publish,
_enrich_sonos_with_discogs, _lookup_discogs_release,
_apply_discogs_release_to_payload, _tracklist_from_release,
_rewrite_art_url_for_overrides, _enrich_with_queue, _find_current_in_queue,
sonos_repoll_loop, _run_repoll_tick, _repoll_should_skip, _build_synthetic_event.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import recognize_proto

from nowplaying import art_overrides
from nowplaying.discogs import catalog as discogs_catalog
from nowplaying.sonos.listener import poll_queue, poll_track
from nowplaying.vinyl import fingerprint as _fp
from nowplaying.orchestrator.streaming_idle import (
    HEARTBEAT_INTERVAL_S,
    RECOGNITION_LEAD_S,
)
from nowplaying.orchestrator.prediction import enrich_guess_contract

log = logging.getLogger("nowplaying.main")


def _apply_clean_display_title(payload: dict) -> None:
    """Rewrite display titles to the cleaned ``clean_title`` (mix/remaster/
    year annotations stripped): both the top-level now-playing title AND
    every tracklist entry's title (the kiosk renders each row's ``title``).

    Central choke point: every vinyl publish routes through
    ``_anchor_and_publish``, so cleaning here covers all cascade branches
    uniformly — recognize, F3/F4 fingerprint, predicted-advance, needs-id —
    instead of relying on each payload builder to clean its own title.
    Safe to overwrite display titles here: upstream position matching uses
    fresh catalog data (not this payload), and advance/F3 match by position,
    not title. No-op for entries without a ``clean_title``.
    """
    tracklist = payload.get("tracklist") or []
    for tr in tracklist:
        clean = tr.get("clean_title")
        if clean:
            tr["title"] = clean
    pos = payload.get("track_position")
    if not pos:
        return
    for tr in tracklist:
        if (tr.get("position") or tr.get("track_position")) == pos:
            clean = tr.get("clean_title")
            if clean:
                payload["title"] = clean
            return


def _art_url_for_release(release_id: int) -> str:
    """Canonical art URL for a release_id, with ?v=<epoch> appended when
    a user override exists so browser caches refresh after picks.

    Without the version param, Chrome will serve the cached art for up
    to 24h (per the art_handler's Cache-Control), and a refresh after
    picking new art shows the stale image. Versioning the URL is the
    cheap fix — different URL → no cache hit → fresh fetch.

    Sync because both lookups are cache-backed (rid_to_album's lru_cache
    + art_overrides' in-memory index); they're safe to call from the
    publish path without an async hop.
    """
    artist_album = discogs_catalog.rid_to_album(release_id)
    if artist_album:
        artist, album = artist_album
        ov = art_overrides.get(artist, album)
        if ov is not None:
            return f"/art/{release_id}?v={ov.picked_at_epoch}"
    return f"/art/{release_id}"


class PublishEnrichmentMixin:
    """Payload enrichment and broadcast-publish helpers.

    All state is accessed via ``self.state``, ``self.bcast``, and
    ``self.sonos_coord`` — owned by ``Orchestrator.__init__``.
    No ``__init__`` defined here.
    """

    def _attach_pending_guess(self, payload: dict) -> None:
        """Attach (and clear) any pending track-guess produced by the
        Shazam-miss + fingerprint-miss path. Single canonical point of
        consumption — see docs/features/llm-track-guess/.

        Drop the guess silently when it disagrees with the payload's
        published track_position. This protects against the dual-display
        bug seen 2026-05-22 (Hum YPAA side flip): the LLM track-guess
        said A1 ("Little Dipper"), but the predicted-advance heuristic
        had already advanced to B6/B7 via the streak path. Attaching
        the stale A1 guess as a separate ``guess`` field caused the
        kiosk to render a BEST GUESS card for A1 ON TOP OF the B6/B7
        track surface — confusing dual UI. The guess is only meaningful
        when it AGREES with what we're publishing.
        """
        state = self.state
        if state.pending_guess is None:
            return
        guess_pos = state.pending_guess.get("position")
        published_pos = payload.get("track_position")
        # Attach only when positions match (or the payload has no
        # position yet — NEEDS_ID payloads). Otherwise drop silently.
        if published_pos is None or guess_pos == published_pos:
            payload["guess"] = state.pending_guess
            enrich_guess_contract(payload)
        state.pending_guess = None

    def _adopt_heuristic_anchor(self, payload: dict, identity: tuple) -> None:
        """Compute and stamp a heuristic anchor for a newly-identified track
        (used when the payload lacks a precise track_started_at — e.g. Shazam).
        """
        state = self.state
        method = payload.get("match_method", "")
        lead = RECOGNITION_LEAD_S.get(method, 0)
        anchor = datetime.now(timezone.utc) - timedelta(seconds=lead)
        state.track_started_at = anchor.isoformat(timespec="seconds").replace(
            "+00:00", "Z",
        )
        state.last_published_identity = identity

    def _attach_learned_fingerprint_count(self, payload: dict) -> None:
        """Stamp ``learned_fingerprint_count`` for payloads with a
        ``(release_id, track_position)`` identity.

        Drives the SomethingWrongPicker's "Forget what I taught" row —
        which only shows when the count is > 0. Computed cheap: one
        indexed COUNT(*) on fp_refs per publish; the index on
        ``release_id`` keeps it sub-millisecond even at hundreds of refs.
        """
        rid = payload.get("release_id")
        pos = payload.get("track_position")
        if rid is None or not pos:
            return
        try:
            payload["learned_fingerprint_count"] = _fp.count_refs_for_track(
                release_id=int(rid), track_position=str(pos),
            )
        except Exception as e:  # noqa: BLE001 — never let a count failure block publish
            log.warning("learned_fingerprint_count query failed: %r", e)

    def _anchor_and_publish(self, payload: dict) -> dict:
        """Stamp track_started_at on the payload so client-side elapsed-time
        clocks (lyrics scroll, side timer) line up with the audio.

        Sonos-supplied anchors are adopted verbatim on track change. Shazam
        hits get a heuristic anchor derived from match latency.
        """
        state = self.state
        # Central display-title cleaning — every publish path lands here, so
        # all cascade branches show the cleaned title (not just the builders
        # that were patched individually).
        _apply_clean_display_title(payload)
        precise = payload.get("track_started_at")
        identity = (
            payload.get("artist"),
            payload.get("title"),
            payload.get("track_position"),
        )
        has_id = any(v for v in identity)
        if precise:
            track_changed = (
                identity != state.last_published_identity
                or state.track_started_at is None
            )
            if track_changed:
                state.track_started_at = precise
                state.last_published_identity = identity
            payload["track_started_at"] = state.track_started_at
            self._attach_pending_guess(payload)
            self._attach_learned_fingerprint_count(payload)
            self._keep_locked_track_confirmed(payload)
            return payload
        if has_id and identity != state.last_published_identity:
            self._adopt_heuristic_anchor(payload, identity)
        if state.track_started_at and has_id:
            payload["track_started_at"] = state.track_started_at
        self._attach_pending_guess(payload)
        self._attach_learned_fingerprint_count(payload)
        self._keep_locked_track_confirmed(payload)
        return payload

    def _keep_locked_track_confirmed(self, payload: dict) -> None:
        """A track the user locked must render confirmed — never as a guess —
        for as long as it's the track on screen, even after its hold decays.

        When a predicted/guess publish targets the SAME (release, position) as
        the active user pin, it's the locked track re-asserting itself (the
        window estimate is still inside it), not a new guess: strip the
        ``predicted`` flag and restore the confirmed identity. Decay governs
        yielding to a *different* track, not relabeling the locked one. A
        same-position predicted-advance is therefore a display no-op.
        See docs/features/locked-track-stays-confirmed/.
        """
        if not payload.get("predicted"):
            return
        pin = self.state.user_track_pin
        if not isinstance(pin, dict):
            return
        same = (
            payload.get("release_id") == pin.get("release_id")
            and (payload.get("track_position") or "").strip().upper()
            == (pin.get("track_position") or "").strip().upper()
        )
        if not same:
            return
        payload["predicted"] = False
        payload["match_method"] = "user-identified"
        payload["match_confidence"] = "user"
        payload.pop("guess", None)

    def _enrich_sonos_with_discogs(self, payload: dict) -> dict:
        """If an AirPlay or streaming payload has artist + title but no
        release_id, try to find a matching release in the user's local
        Discogs collection. On hit, patch the payload with release_id,
        side, and track_position — but never with art; the streaming
        service's own cover art always wins (see
        ``_apply_discogs_release_to_payload``). We
        intentionally do NOT attach the Discogs tracklist on streaming
        sources — the streaming service already provides a real queue
        via Sonos's Queue:1 service (see _enrich_with_queue below), so
        the album tracklist would be redundant and misleading (album
        order ≠ play order on streaming).
        """
        if payload.get("source") not in ("airplay", "streaming"):
            return payload
        if payload.get("release_id") is not None:
            return payload
        artist = (payload.get("artist") or "").strip()
        title = (payload.get("title") or "").strip()
        if not artist or not title:
            return payload
        rel = self._lookup_discogs_release(artist, title)
        if not rel:
            return payload
        enriched = self._apply_discogs_release_to_payload(payload, rel)
        log.info(
            "%s enriched via discogs: artist=%r title=%r release_id=%s pos=%s",
            payload.get("source"), artist, title,
            rel.get("id"), rel.get("matched_track_position"),
        )
        return enriched

    def _lookup_discogs_release(self, artist: str, title: str) -> dict | None:
        """Wrap discogs_catalog.find_by_artist_title with exception logging."""
        try:
            return discogs_catalog.find_by_artist_title(
                artist=artist,
                title=title,
                preferred_release_id=None,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("airplay discogs enrich failed: %r", e)
            return None

    def _apply_discogs_release_to_payload(
        self, payload: dict, rel: dict,
    ) -> dict:
        """Build an enriched payload from a Discogs release match: patch
        release_id + album metadata and the AirPlay-only tracklist
        (streaming uses Sonos Queue:1 instead). Art is left alone unless
        the payload arrived without any.
        """
        enriched = dict(payload)
        enriched["release_id"] = rel.get("id")
        enriched["album"] = rel.get("title") or enriched.get("album")
        enriched["year"] = rel.get("year") or enriched.get("year")
        enriched["label"] = rel.get("label") or enriched.get("label")
        enriched["catno"] = rel.get("catno") or enriched.get("catno")
        matched_position = rel.get("matched_track_position")
        if matched_position:
            enriched["track_position"] = matched_position
            enriched["side"] = matched_position[:1] if matched_position else None
        # Do NOT overwrite the service-supplied art. On AirPlay and
        # streaming, Sonos already told us exactly which track and which
        # cover is playing; the Discogs match is a metadata convenience,
        # and its vinyl scan is frequently a different pressing than what
        # the service is streaming. Only fill art in when Sonos gave us
        # none at all — empty art is worse than approximate art.
        if not enriched.get("art_url") and rel.get("id") is not None:
            enriched["art_url"] = _art_url_for_release(int(rel["id"]))
        if payload.get("source") == "airplay":
            tracklist = self._tracklist_from_release(rel)
            if tracklist:
                enriched["tracklist"] = tracklist
        return enriched

    @staticmethod
    def _tracklist_from_release(rel: dict) -> list[dict]:
        """Project the release's tracks into the kiosk's tracklist shape
        (position/side/title/duration). Returns [] when the release has
        no tracks. AirPlay-only — streaming uses the real Sonos queue.
        """
        tracks = rel.get("tracks") or []
        return [
            {
                "position": t.get("position"),
                "side": t.get("side") or (t.get("position", "")[:1] if t.get("position") else None),
                "title": t.get("title"),
                "duration_seconds": t.get("duration_seconds"),
            }
            for t in tracks
        ]

    def _rewrite_art_url_for_overrides(self, payload: dict) -> dict:
        """If a non-vinyl payload has artist+album and an art override
        exists for that ``(artist, album)``, rewrite ``art_url`` to
        ``/art-by-name`` so the override is served by the kiosk. When no
        override exists, leave ``art_url`` untouched — typically a
        perfectly good streaming-service URL routed through the
        orchestrator's ``/art-cache/...`` proxy.

        Runs for matched streams too, not just unmatched ones: streaming
        art no longer routes through ``/art/<rid>``, so this is now the
        only place a deliberate user pick can beat the service's art.

        Override-conditional is the load-bearing UX choice. Unconditional
        rewriting would degrade Sonos's good art to a 404 every time the
        user hasn't picked one (no MusicBrainz fallback exists for
        non-matched tracks; see ``art_by_name_handler``).
        """
        if payload.get("source") not in ("airplay", "streaming"):
            return payload
        artist = (payload.get("artist") or "").strip()
        album = (payload.get("album") or "").strip()
        if not artist or not album:
            return payload
        ov = art_overrides.get(artist, album)
        if ov is None:
            return payload
        out = dict(payload)
        # Include the override's epoch as a cache-bust so the browser
        # fetches fresh after a pick instead of serving the stale
        # cached image at the un-versioned URL.
        params = {"artist": artist, "album": album, "v": str(ov.picked_at_epoch)}
        out["art_url"] = f"/art-by-name?{urlencode(params)}"
        return out

    async def _enrich_with_queue(self, payload: dict) -> dict:
        """Attach the next N Sonos queue items to streaming payloads.

        AirPlay queues live on the sender device, not on Sonos, so this
        no-ops there. Native Sonos streaming sources (Spotify, Tidal,
        Apple Music via Sonos, library, SonosFavorite playlists) expose
        the queue through Queue:1 UPnP — we render the next few items
        as an 'Up Next' panel on the kiosk.
        """
        if payload.get("source") != "streaming":
            return payload
        if self.sonos_coord is None:
            return payload
        try:
            items = await poll_queue(self.sonos_coord, limit=16)
        except Exception as e:  # noqa: BLE001
            log.warning("queue poll exception: %r", e)
            return payload
        if not items:
            return payload
        enriched = dict(payload)
        enriched["queue"] = items
        current_index = self._find_current_in_queue(payload, items)
        if current_index is not None:
            enriched["queue_position"] = current_index
        return enriched

    @staticmethod
    def _find_current_in_queue(payload: dict, items: list[dict]) -> int | None:
        """Locate the currently-playing track inside the Sonos queue items
        list by exact (title, artist) match. Returns the 0-based index or
        None when the track isn't found.
        """
        cur_title = (payload.get("title") or "").strip().lower()
        cur_artist = (payload.get("artist") or "").strip().lower()
        for i, item in enumerate(items):
            if (
                (item.get("title") or "").strip().lower() == cur_title
                and (item.get("artist") or "").strip().lower() == cur_artist
            ):
                return i
        return None

    async def sonos_repoll_loop(self) -> None:
        """Sonos UPnP doesn't reliably push events on every within-session
        track change — the URI and TransportState often stay the same.
        We poll Sonos every HEARTBEAT_INTERVAL_S during an AirPlay or
        streaming session and dispatch a synthetic event when the
        polled (title, artist) differs from what's currently published.

        Polling Sonos is the documented mechanism for this and what
        Sonos's own apps do. Vinyl heartbeat audio recognition handles
        the vinyl path. AirPlay that opens without Sonos metadata is
        recovered by this loop — the heartbeat cascade is inert for
        airplay (see on_heartbeat), so there is no audio path to fall
        through to. TV / radio push their own events.
        """
        stop = self.stop
        sonos_coord = self.sonos_coord
        if sonos_coord is None:
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                # Expected: heartbeat-interval elapsed, time to repoll.
                log.debug("sonos_repoll_loop: heartbeat interval elapsed; running tick")
            await self._run_repoll_tick(sonos_coord)

    async def _run_repoll_tick(self, sonos_coord) -> None:
        """One iteration of the sonos repoll loop: gate on source/state,
        poll Sonos, and dispatch a synthetic event when the polled track
        differs from what's currently published (or unconditionally on
        streaming so queue position advances). Runs for airplay even when
        no metadata has arrived yet — that is exactly the state this loop
        exists to recover from.
        """
        state = self.state
        if state.sonos_source not in ("airplay", "streaming"):
            return
        # Deliberately NOT gated on state.sonos_has_metadata. That flag
        # conflates "Sonos has no metadata for this source" (permanent,
        # system-audio airplay) with "Sonos has not published metadata to
        # us yet" (transient, the session-open DIDL race). Gating here on
        # the first meaning starved the second: a session that opened with
        # an empty DIDL could never recover, because this loop is the only
        # periodic path that pulls the late metadata across. The no-title
        # guard below makes the genuinely-metadata-less case a cheap no-op.
        try:
            polled = await poll_track(sonos_coord)
        except Exception as e:  # noqa: BLE001
            log.warning("sonos repoll exception: %r", e)
            return
        if not polled or not polled.get("title"):
            return
        cached = state.last_vinyl or {}
        if self._repoll_should_skip(cached, polled):
            return
        log.info(
            "%s repoll: track now %s — %s",
            state.sonos_source, polled.get("artist"), polled.get("title"),
        )
        await self.on_sonos_event(self._build_synthetic_event(cached, polled))

    def _repoll_should_skip(self, cached: dict, polled: dict) -> bool:
        """No track change → skip re-emit on airplay (no queue to advance).
        On streaming we still re-emit so the queue position can shift.
        """
        state = self.state
        if (
            cached.get("title") == polled.get("title")
            and cached.get("artist") == polled.get("artist")
        ):
            return state.sonos_source != "streaming"
        return False

    def _build_synthetic_event(self, cached: dict, polled: dict) -> dict:
        """Build the synthetic Sonos event passed to on_sonos_event from
        a repoll tick.
        """
        state = self.state
        synthetic = {
            "ts": recognize_proto.now_iso(),
            "state": "PLAYING",
            "source": state.sonos_source,
            "title": polled["title"],
            "artist": polled["artist"],
            "album": polled.get("album"),
            "album_art": polled.get("album_art"),
            "duration": None,
            "uri": cached.get("uri"),
            "raw_uri_prefix": cached.get("raw_uri_prefix"),
            "didl_was_empty": False,
            "sonos_polled": True,
        }
        if polled.get("duration_s") is not None:
            synthetic["duration_seconds"] = int(polled["duration_s"])
        return synthetic
