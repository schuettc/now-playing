import { appendAttemptSuffix } from './crossfadeUrl';

// Retry schedule for transient /art/<id> 404s. The orchestrator's MusicBrainz
// lookup is async and can land the file on disk a few seconds after the WS
// payload first announces the release.
const RETRY_DELAYS_MS = [5000, 15000, 30000];

/**
 * Load state of one art layer. `target` is the URL the layer was asked to
 * show; `shown` is the URL of the last image that actually loaded, which
 * stays on screen while a new target loads so a same-album URL change
 * swaps in place.
 */
export interface ArtLoadState {
  target: string;
  attempt: number;
  phase: 'loading' | 'loaded' | 'waiting' | 'failed';
  shown: string | null;
}

export type ArtLoadAction =
  | { type: 'target'; src: string }
  | { type: 'loaded'; url: string }
  | { type: 'error'; url: string }
  | { type: 'retry' };

export function initialArtLoad(src: string): ArtLoadState {
  return { target: src, attempt: 0, phase: 'loading', shown: null };
}

/** The URL to fetch now, or null when nothing is in flight. */
export function loadingUrlOf(s: ArtLoadState): string | null {
  if (s.phase !== 'loading') return null;
  return appendAttemptSuffix(s.target, s.attempt) ?? null;
}

/** Delay before the next retry, or null when no retry is pending. */
export function retryDelayOf(s: ArtLoadState): number | null {
  return s.phase === 'waiting' ? RETRY_DELAYS_MS[s.attempt] : null;
}

/**
 * True once the target has failed to load and there is no image to keep. A
 * layer shows one album, so an image it already loaded is that album's cover
 * and stays when a later URL fails.
 */
export function showsNoArt(s: ArtLoadState): boolean {
  if (s.shown !== null || s.phase === 'loaded') return false;
  return s.phase !== 'loading' || s.attempt > 0;
}

function onError(s: ArtLoadState): ArtLoadState {
  if (s.attempt >= RETRY_DELAYS_MS.length) return { ...s, phase: 'failed' };
  return { ...s, phase: 'waiting' };
}

export function artLoadReducer(s: ArtLoadState, action: ArtLoadAction): ArtLoadState {
  switch (action.type) {
    case 'target':
      if (action.src === s.target) return s;
      return { target: action.src, attempt: 0, phase: 'loading', shown: s.shown };
    case 'loaded':
      if (action.url !== loadingUrlOf(s)) return s;
      return { ...s, phase: 'loaded', shown: action.url };
    case 'error':
      return action.url === loadingUrlOf(s) ? onError(s) : s;
    case 'retry':
      if (s.phase !== 'waiting') return s;
      return { ...s, attempt: s.attempt + 1, phase: 'loading' };
  }
}
