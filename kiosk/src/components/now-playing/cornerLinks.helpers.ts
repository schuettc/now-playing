import type { NowPlaying } from '@/types';

export interface CornerLinksInput {
  data: NowPlaying | null;
  showTrack: boolean;
  showNeedsId: boolean;
  showVinylIdentifying: boolean;
}

export interface CornerLinksView {
  discogsReleaseId: number | null;
}

/**
 * Derives the surviving CornerLinks data (Discogs link only).
 *
 * The previous `↺ Wrong track? / Wrong album?` cluster was retired
 * by `identify-learning-chip-undo-strip` — `UndoStrip` is now the
 * single "wrong track" affordance, anchored under the StatusPill.
 * Wikipedia link is rendered by `WikipediaLink` directly using its
 * own store-backed lookup.
 */
export function buildCornerLinks(input: CornerLinksInput): CornerLinksView {
  const { data, showTrack } = input;
  const releaseId = data?.release_id;
  const lockedReleaseId =
    showTrack && releaseId !== undefined ? releaseId : null;
  return {
    discogsReleaseId: lockedReleaseId,
  };
}
