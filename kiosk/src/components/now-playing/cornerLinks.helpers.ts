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
 * The `↺ Wrong track? / Wrong album?` cluster that used to live here
 * is gone; those corrections are reached through the "Something wrong?"
 * picker.
 * Wikipedia link is rendered by `WikipediaLink` directly using its
 * own store-backed lookup.
 */
export function buildCornerLinks(input: CornerLinksInput): CornerLinksView {
  const { data, showTrack } = input;
  const releaseId = data?.release_id;
  const lockedReleaseId =
    showTrack && releaseId != null ? releaseId : null;
  return {
    discogsReleaseId: lockedReleaseId,
  };
}
