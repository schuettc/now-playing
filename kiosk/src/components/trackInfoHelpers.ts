import type { NowPlaying } from '@/types';

/**
 * "Side A1" prefix implies vinyl playback. On AirPlay it's misleading
 * even when Discogs enrichment populated track_position — the user is
 * streaming, not on physical side A.
 */
export function getSidePrefix(data: Pick<NowPlaying, 'source' | 'track_position'>): string | null {
  if (data.source !== 'vinyl') return null;
  return data.track_position ? data.track_position : null;
}

export function joinCredits(label: string | undefined, catno: string | undefined): string | null {
  const parts: string[] = [];
  if (label) parts.push(label);
  if (catno) parts.push(catno);
  return parts.length > 0 ? parts.join(' · ') : null;
}
