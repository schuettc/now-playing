import type { MatchMethod, NowPlaying } from '@/types';

// Vinyl payloads render the track surface only when the orchestrator
// has either a real recognition OR a predicted-advance guess. Anything
// else (unmatched, undefined match_method) routes to vinyl-identifying
// so the kiosk never shows "Unknown Track" / null metadata.
//
// `predicted` is intentionally included pending separate review of
// predicted-advance render behavior — this gate must not alter what
// the kiosk shows for match_method === 'predicted'.
const VINYL_RENDER_AS_TRACK: ReadonlySet<MatchMethod> = new Set<MatchMethod>([
  'shazam',
  'sonos-didl',
  'sonos-polled',
  'fingerprint',
  'user-identified',
  'user-selected',
  'predicted',
]);

/**
 * Discriminated state describing what the kiosk should be rendering.
 * Replaces the cascade of overlapping `show*` booleans that used to
 * live inline in `NowPlaying.tsx`.
 *
 * Precedence (matching the historical boolean order):
 *  1. idle — no payload, or playback STOPPED
 *  2. needs-id — NEEDS_ID screen takes precedence over title-less branches
 *  3. airplay — AirPlay source with no title
 *  4. vinyl-identifying — vinyl source with no title (spinner)
 *  5. track — everything else (a renderable track)
 */
export type ScreenState =
  | { kind: 'idle' }
  | { kind: 'needs-id'; data: NowPlaying }
  | { kind: 'airplay'; data: NowPlaying }
  | { kind: 'vinyl-identifying'; data: NowPlaying }
  | { kind: 'track'; data: NowPlaying; isPaused: boolean };

/**
 * Pure derivation — no React state of its own. Exported as a function
 * (not a hook) so it can be unit-tested without rendering.
 *
 * Note: vinyl is *excluded* from the paused state. On vinyl, "paused"
 * isn't a meaningful playback signal — the path is needle-up → silence
 * → idle timer → STOPPED, not a UPnP pause event. TRANSITIONING is
 * also excluded to avoid flicker during the brief play↔pause UPnP
 * transition that Sonos emits on streaming.
 */
/**
 * True when the screen state is a NEEDS_ID escalation on a vinyl source.
 * NowPlayingView uses this to exclude vinyl NEEDS_ID from TrackSurface and
 * TrackBackdrop — rendering TrackLayout with a null-title vinyl payload would
 * produce the "Unknown Track / Unknown Artist / NO ART" failure UI.
 */
export function isVinylNeedsId(state: ScreenState): boolean {
  return state.kind === 'needs-id' && state.data.source === 'vinyl';
}

/**
 * True when ScreenOverlay should render the VinylIdentifying spinner.
 * Covers both the normal identifying phase and the NEEDS_ID escalation
 * on vinyl (orchestrator gave up after N unmatched heartbeats).
 */
export function showsVinylOverlay(state: ScreenState): boolean {
  return state.kind === 'vinyl-identifying' || isVinylNeedsId(state);
}

export function deriveScreenState(data: NowPlaying | null): ScreenState {
  if (!data || data.state === 'STOPPED') return { kind: 'idle' };
  if (data.state === 'NEEDS_ID') return { kind: 'needs-id', data };
  if (data.source === 'airplay' && !data.title) {
    return { kind: 'airplay', data };
  }
  if (
    data.source === 'vinyl' &&
    !VINYL_RENDER_AS_TRACK.has(data.match_method as MatchMethod)
  ) {
    return { kind: 'vinyl-identifying', data };
  }
  const isPaused =
    (data.state === 'PAUSED_PLAYBACK' || data.state === 'PAUSED') &&
    data.source !== 'vinyl';
  return { kind: 'track', data, isPaused };
}
