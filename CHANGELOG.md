# Changelog

All notable changes to Now Playing. Going forward, entries are added per release.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Versioning is [SemVer](https://semver.org/).

---

## [1.2.0] — 2026-05-28

On-screen touch keyboard. The lookup search — the only text field on the otherwise tap-only kiosk — now raises an in-app virtual keyboard, so manual artist/album/catalog search works on the touchscreen without a physical keyboard attached.

### Kiosk UX

- **kiosk-touch-keyboard** — New `VirtualKeyboard` touch primitive (digit row + QWERTY, space/backspace/clear/Done) wired to the `/lookup` search field. Keys retain input focus via `pointerdown` `preventDefault` so the caret never drops; append/backspace edit at the end of the query; the keyboard fixes to the viewport bottom and the results grid pads to stay clear. `SearchField` now forwards a ref to its `<input>`, which also makes the existing `useIdentifyScope` mount-focus fire (previously a silent no-op).

[1.2.0]: https://github.com/schuettc/now-playing/releases/tag/v1.2.0

---

## [1.1.0] — 2026-05-27

No-Discogs experience. The album-context layer (art, tracklist, side timer, BEST GUESS, local fingerprint cascade) works for records that aren't in your Discogs collection — discovered via MusicBrainz on first play, persisted to a parallel `discovered.sqlite`, and fingerprinted the same way as Discogs-synced releases.

### Recognition pipeline

- **shazam-enrichment-plumbing** — Shazam wrapper extracts `album`, `art_url`, and `albumadamid`; the shazam-only branch of the recognize cascade now propagates these fields through to the publish payload.
- **discovered-track-position-resolution** — Shazam's track title is matched against the MB-discovered tracklist to set `track_position` and `side`, mirroring the Discogs path so the side timer and tracklist highlight work without a Discogs sync.
- **shazam-publish-and-discovery-fixes** — `to_now_playing_vinyl` propagates `art_url`, `release_mbid`, and `albumadamid` on the no-Discogs branch. Album-name normalization strips edition suffixes (e.g. `(Deluxe Edition)`) so Shazam's release titles resolve to canonical MB titles.

### Discovery layer (new)

- **musicbrainz-tracklist-discovery** — New `pi/nowplaying/discovery/` module + parallel `pi/data/discovered.sqlite`. On a Shazam hit with no Discogs match, a background MB lookup persists the release + tracklist; subsequent heartbeats find it locally and attach `release_mbid` + tracklist to the payload.
- **catalog-dispatcher** — New `pi/nowplaying/catalog/` module routes reads/writes between `discogs.sqlite` and `discovered.sqlite` by lock shape, so the recognize cascade and downstream consumers are catalog-agnostic.
- **discovered-fingerprint-promotion** — MBID-keyed `fp_refs` / `fp_hashes` tables in `discovered.sqlite`. The cascade match dispatcher routes by lock shape and the promotion dispatcher writes to the right store, so the local fingerprint cascade applies to MB-discovered releases identically to Discogs-synced ones.

### Schema

- **discovered.sqlite** — new database parallel to `discogs.sqlite`: release + track tables plus MBID-keyed fingerprint refs/hashes.

### Bug fixes

- **catalog-graceful-missing-db** — Catalog entrypoints wrap `sqlite3.OperationalError` so the orchestrator boots and runs without a Discogs DB on disk (rather than crashing on the first lookup).

[1.1.0]: https://github.com/schuettc/now-playing/releases/tag/v1.1.0

---

## [1.0.0] — 2026-05-27

Initial public release.

### Recognition pipeline

- **shazam-recognition** — ShazamIO is the primary recognizer; clips are captured at 12s (below Shazam's 15s silent-reject cliff) and gated on a silence floor with audible-event debounce so threshold-flap doesn't reset per-side state.
- **album-disambiguation** — Discogs scoring when a track appears on multiple releases: sticky-release (+25, downgraded to +5 when sticky is on a deep side and another candidate has the side-first claim), side-first (+15, rowid-aware so cumulative-numbered multi-LPs qualify), vinyl-format (+5), compilation penalty (-3).
- **tracklist-aware-advancement** — When recognition returns unmatched on an album-locked side, advance through the locked release's tracklist. Predicted tracks carry `match_method: "predicted"`; Shazam confirms supersede predictions; sustained silence clears them.
- **ambiguity-selection** — Close-scoring alternates are surfaced in the AdminOverlay; picking one rebuilds the payload and republishes.
- **shazam-only-display** — Records not in your Discogs catalog appear via cross-heartbeat agreement on the same `(artist, title)` from Shazam.
- **airplay-audio-recognition** — System-audio AirPlay (no metadata) gets identified via the UFO202 pipeline alongside vinyl.
- **sonos-streaming-metadata** — Empty AVTransport DIDL triggers polled enrichment via `get_current_track_info()`. Apple Music / Spotify AirPlay metadata renders correctly.
- **sonos-watchdog-reconcile** — After a watchdog-driven resubscription, poll actual transport + track state instead of trusting the first NOTIFY, which can carry stale source data.
- **unmatched-streak-idle** — Four consecutive unmatched heartbeats reverts to fast cadence and arms a 45s idle timer.
- **anticipated-track-end** — SIGUSR1/SIGUSR2 between orchestrator and capture drops cadence from 15s to 5s as a track approaches its known end. New-track latency near a boundary drops from ~25s to ~10s.
- **musicbrainz-duration-enrichment** — Per-track durations missing from Discogs (~50% of catalog) are backfilled from MusicBrainz with a track-count-aware MBID resolver. `--only enrich-durations` runs a one-time backfill; MBIDs are cached.
- **fingerprint-cascade** — Optional local-fingerprint module (`FINGERPRINT_ENABLED=true`, `uv sync --extra fingerprint`) that passively trains a fingerprint DB from Shazam-confirmed clips and identifies tracks Shazam misses on previously-played albums.
- **llm-assist** — Optional Anthropic Haiku 4.5 hooks (`ANTHROPIC_API_KEY`, `uv sync --extra llm`) at four cascade decision points: cover/tribute filter, end-of-side advance, `/identify` release ranker, and fingerprint promotion gate. Falls back to heuristics without the key.
- **help-identify-track** — `NEEDS_ID` state stashes the clip and surfaces a tracklist-predicted next track for one-tap confirmation. BEST GUESS card renders the predicted-payload shape on both the predicted-advance and state-decay paths.

### Kiosk UX

- **smooth-transitions** — Track changes crossfade rather than cut. Backdrop leads, art follows ~100ms behind, text trails another ~100ms.
- **color-gradient** — Backdrop tinted by the dominant color of the album art via a hidden 32×32 canvas sample.
- **side-timer** — Top-right overlay showing time remaining on the current track and side.
- **touch-override** — Long-press → admin overlay (mark-wrong, force-promote, switch alternates). Mouse-clickable `···` button for non-touch users.
- **identify-page** — `/identify` is its own scroll container so long tracklists (28+ tracks on multi-LP releases) are reachable. Search-and-pick has explicit × close on expanded albums, hoist-to-top reorder, and scroll-into-view follow-up. "Wrong album" scope prefills the search with `<artist> <title>` and one-tap-resolves the track on whichever album the user picks.

### Source / state handling

- **heartbeat-source-gate** — Capture pauses heartbeat emission when the source isn't vinyl or no-metadata AirPlay. Saves ~400MB/hour of orphan clips.

### Productization

- **onboarding-walkthrough** — `docs/INSTALL.md` end-to-end install guide.
- **installer-skills** — `.claude/skills/` bundle: setup, troubleshooting, status, diagnostics.
- **landing-page** — README: hero, dataflow diagram, hardware BoM, status disclosure, acknowledgments.
- **systemd-services** — Two units (`nowplaying-orchestrator.service`, `nowplaying-kiosk.service`) + portable installer that substitutes user/home into templates.
- **play-history** — Persistent SQLite log of every confirmed play, exposed via `GET /history?limit=N&since=UNIX_TS`.
- **lastfm-scrobbling** — Optional Last.fm scrobbling for confirmed tracks (≥30s tracks, ≥50% played or ≥240s). Disabled when credentials are absent.

[1.0.0]: https://github.com/schuettc/now-playing/releases/tag/v1.0.0
