import type { NowPlaying } from '@/types';

/**
 * Admin overlay is available whenever there's something for the
 * overlay to act on. Vinyl always counts (even unmatched Shazam —
 * Wrong-song / Next-track still apply). Non-vinyl counts when we
 * have ANY of: release_id (full picker), or album (the by-name
 * picker path can find CAA candidates and persist an override).
 */
export function computeAdminAvailable(data: NowPlaying | null): boolean {
  return Boolean(
    data &&
      data.title &&
      (data.release_id !== undefined ||
        data.album ||
        data.source === 'vinyl'),
  );
}
