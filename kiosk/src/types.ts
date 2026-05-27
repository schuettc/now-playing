export type PlaybackState =
  | 'PLAYING'
  | 'PAUSED'            // forward-compat alias if orchestrator normalizes
  | 'PAUSED_PLAYBACK'   // what Sonos's UPnP transport_state actually emits
  | 'STOPPED'
  | 'TRANSITIONING'
  | 'NEEDS_ID';

export type Source = 'vinyl' | 'streaming' | 'radio' | 'airplay' | 'tv' | 'unknown';

export type MatchMethod =
  | 'shazam'
  | 'fingerprint'
  | 'predicted'
  | 'unmatched'
  | 'sonos-didl'
  | 'sonos-polled'
  | 'user-selected'
  | 'user-identified';

export interface NeedsIdPrevious {
  release_id?: number;
  track_position?: string;
  title?: string;
  artist?: string;
  art_url?: string;
}

export interface AlternateRelease {
  release_id: number;
  album: string;
  year?: number;
  format?: string;
  track_position?: string;
  track_title?: string;
  score?: number;
}

export interface TracklistItem {
  position: string;
  side: string | null;
  title: string;
  duration_seconds: number;
}

/**
 * One item from the Sonos playback queue (Queue:1 UPnP service).
 * Populated only for streaming sources — AirPlay queues live on the
 * sender device and aren't exposed via Sonos.
 */
export interface QueueItem {
  title: string | null;
  artist: string | null;
  album: string | null;
}

/**
 * Optional best-guess for the currently-playing track on the locked
 * album, published on Shazam-miss + fingerprint-miss heartbeats.
 * Either LLM-sourced (when `ANTHROPIC_API_KEY` is set on the Pi) or
 * heuristic (from the existing `predicted_position`).
 *
 * `alt` is the medium-confidence two-candidate case; surfaced only in
 * the overlay variant of the UI's `GuessConfirm` component (banner /
 * inline variants render only the primary guess). Backend emits all
 * confidence levels — UI picks the visual variant. Refs are NEVER
 * written from this guess; only user confirmation (the pin path) does.
 *
 * See docs/features/llm-track-guess/.
 */
export interface Guess {
  position: string;
  title: string;
  confidence: 'high' | 'medium' | 'low';
  source: 'llm' | 'heuristic';
  alt?: { position: string; title: string };
}

export interface NowPlaying {
  ts: string;
  state: PlaybackState;
  source: Source;
  title?: string;
  artist?: string;
  album?: string;
  year?: number;
  art_url?: string;
  release_id?: number;
  /** MusicBrainz release MBID — populated on the discovered-release
   * path (no-Discogs trilogy). Additive optional; kiosk display logic
   * unchanged today, future "Wrong album" / MB-candidate flows can
   * consume it. */
  release_mbid?: string;
  /** Apple Music album identifier from Shazam's wrapper. Additive
   * optional; reserved for future MusicKit integrations. */
  albumadamid?: string;
  label?: string;
  catno?: string;
  track_position?: string;
  side?: string;
  tracklist?: TracklistItem[];
  match_method?: MatchMethod;
  match_confidence?: number | string;
  /**
   * True when this payload was inferred from the locked album's tracklist
   * because Shazam couldn't identify the audio. The kiosk renders predicted
   * tracks with a subtle visual cue (italic + badge) so the user knows it's
   * a best-guess rather than a confirmed Shazam match. See
   * docs/features/tracklist-aware-advancement/.
   */
  predicted?: boolean;
  device_name?: string;
  alternate_releases?: AlternateRelease[];
  /**
   * Number of learned fingerprint refs cached for the current
   * (release_id, track_position) cohort. Populated by
   * `_publish_enrichment._attach_learned_fingerprint_count`. The
   * SomethingWrongPicker's clear-fingerprints row reads this to
   * decide whether to surface itself and renders the count in the
   * confirm dialog. Missing when there's no release_id +
   * track_position to query against.
   */
  learned_fingerprint_count?: number;
  previous?: NeedsIdPrevious;
  track_started_at?: string;
  /**
   * Upcoming items from the Sonos playback queue. Populated for
   * streaming sources only; the index of the currently-playing item is
   * `queue_position` (0-based).
   */
  queue?: QueueItem[];
  queue_position?: number;
  /**
   * Best-guess track for the locked album on Shazam-miss +
   * fingerprint-miss heartbeats. See `Guess`.
   */
  guess?: Guess;
}

/**
 * Identify-confidence state for the unified StatusPill + behavior triggers.
 * Per design spec at docs/features/confirmed-fingerprint-coverage/design-output/.
 *
 * - `confirmed-shazam`: match_method is `shazam`/`sonos-didl`/`sonos-polled`.
 * - `confirmed-local`: match_method is `fingerprint`.
 * - `awaiting-confirm`: `guess` present, no recent user pin.
 * - `identifying`: TRANSIENT — emitted for ~45s after source flip with no
 *   recognition yet. Suppresses the loud "Unknown · help identify" failure UI
 *   while the cascade (Shazam + blind fingerprint scan) is still running.
 *   After 45s without recognition, transitions to `needs-id`.
 * - `needs-id`: state is `NEEDS_ID` or no match available after timeout.
 * - `user-pinned`: TRANSIENT — set on local tap; 50s timer; graduates to
 *   `confirmed-local` (Feature A is writing refs in the background).
 *   Orthogonal to ScreenState; pill state, not screen state.
 */
export type IdentifyState =
  | 'confirmed-shazam'
  | 'confirmed-local'
  | 'awaiting-confirm'
  | 'identifying'
  | 'needs-id'
  | 'user-pinned';

export interface AlbumStats {
  release_id: number;
  play_count: number;
  last_played_at: number | null;
  first_played_at: number | null;
}

export type WireMessage =
  | { type: 'now_playing'; payload: NowPlaying }
  | { type: 'heartbeat'; ts: string };
