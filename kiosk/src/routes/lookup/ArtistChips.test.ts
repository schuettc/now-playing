import { describe, expect, it } from 'vitest';
import { distinctArtists } from './ArtistChips';
import type { RecentPlay } from '@/hooks/useRecentPlays';

function rp(artist: string | null): RecentPlay {
  return {
    release_id: null, track_position: null, artist, title: null, album: null,
    match_method: null, source: null, ts: 0, art_url: undefined,
  };
}

describe('distinctArtists', () => {
  it('returns [] for null recents', () => {
    expect(distinctArtists(null)).toEqual([]);
  });

  it('skips null and whitespace-only artists', () => {
    expect(
      distinctArtists([rp(null), rp('  '), rp('Failure'), rp('')]),
    ).toEqual(['Failure']);
  });

  it('deduplicates preserving first-seen order', () => {
    expect(
      distinctArtists([
        rp('Failure'), rp('Slint'), rp('Failure'), rp('Codeine'),
      ]),
    ).toEqual(['Failure', 'Slint', 'Codeine']);
  });

  it('honors the limit parameter', () => {
    expect(
      distinctArtists(
        [rp('A'), rp('B'), rp('C'), rp('D'), rp('E')],
        3,
      ),
    ).toEqual(['A', 'B', 'C']);
  });

  it('defaults to limit=8', () => {
    const recents = Array.from({ length: 20 }, (_, i) => rp(`Artist ${i}`));
    expect(distinctArtists(recents)).toHaveLength(8);
  });
});
