/**
 * Pure helpers for the `TappableTrackRow` state machine.
 *
 * Two state axes:
 *  - **Persistent visual** (`TapRowState`): `current` | `guess` | `default`.
 *    Derived from `(position, currentPosition, guessPosition)`.
 *  - **Interaction** (`FlashState`): `idle` | `just-tapped`. Local to
 *    each row component; managed by setTimeout, not derived.
 *
 * `current` wins over `guess`: if a payload has both `track_position`
 * and `guess.position` pointing at the same row (rare during
 * transitions), the row IS playing — the guess pointer is stale.
 */
import type { NowPlaying } from '@/types';

export type TapRowState = 'current' | 'guess' | 'default';
export type FlashState = 'idle' | 'just-tapped';

export function rowState(args: {
  position: string;
  currentPosition: string | null | undefined;
  guessPosition: string | null | undefined;
}): TapRowState {
  if (args.currentPosition && args.position === args.currentPosition) {
    return 'current';
  }
  if (args.guessPosition && args.position === args.guessPosition) {
    return 'guess';
  }
  return 'default';
}

/**
 * Gate for whether `TracklistPanel` should render `TappableTrackRow`
 * (vinyl-locked) vs the plain informational `TrackRow` (streaming /
 * no lock).
 *
 * Tapping a streaming-source row has no meaning — there's no
 * `/api/pin-track` semantics for streaming metadata, which is owned
 * by Sonos.
 */
export function shouldUseTappable(
  payload: Pick<NowPlaying, 'source' | 'release_id'> | null | undefined,
): boolean {
  if (!payload) return false;
  if (payload.source !== 'vinyl') return false;
  return typeof payload.release_id === 'number';
}
