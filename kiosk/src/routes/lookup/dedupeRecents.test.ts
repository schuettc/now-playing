import { describe, it, expect } from 'vitest';
import { dedupeRecentsByAlbum } from './dedupeRecents';
import type { RecentPlay } from '@/hooks/useRecentPlays';

function play(p: Partial<RecentPlay>): RecentPlay {
  return {
    release_id: null, track_position: null, artist: null, title: null,
    album: null, match_method: null, source: 'vinyl', ts: 0,
    art_url: undefined, ...p,
  };
}

describe('dedupeRecentsByAlbum', () => {
  it('collapses the live case — 5 rows, 2 distinct albums', () => {
    // Pack Up The Cats ×2 (different tracks) + Beatles 1967-1970 ×3
    // (different tracks / pressings) → 2 cards.
    const recents = [
      play({ artist: 'Local H', album: 'Pack Up The Cats', title: 'Lucky', ts: 50 }),
      play({ artist: 'The Beatles', album: '1967-1970', title: 'Hey Jude', release_id: 11, ts: 49 }),
      play({ artist: 'Local H', album: 'Pack Up The Cats', title: 'Cha! Said The Kitty', ts: 48 }),
      play({ artist: 'The Beatles', album: '1967-1970', title: 'Revolution', release_id: 22, ts: 47 }),
      play({ artist: 'The Beatles', album: '1967-1970', title: 'Get Back', release_id: 11, ts: 46 }),
    ];
    const out = dedupeRecentsByAlbum(recents);
    expect(out.map((r) => r.album)).toEqual(['Pack Up The Cats', '1967-1970']);
    // Keeps the most-recent occurrence (newest-first input → first seen).
    expect(out[0].title).toBe('Lucky');
    expect(out[1].title).toBe('Hey Jude');
  });

  it('dedups case/whitespace-insensitively and across pressings', () => {
    const out = dedupeRecentsByAlbum([
      play({ artist: 'Local H', album: 'Pack Up The Cats', release_id: 1, ts: 2 }),
      play({ artist: ' local h ', album: 'PACK UP THE CATS', release_id: 999, ts: 1 }),
    ]);
    expect(out).toHaveLength(1);
  });

  it('keeps album-less rows distinct (does not collapse unknowns together)', () => {
    const out = dedupeRecentsByAlbum([
      play({ artist: 'X', album: null, release_id: 1, ts: 3 }),
      play({ artist: 'Y', album: null, release_id: 2, ts: 2 }),
      play({ artist: null, album: null, title: 'Mystery', ts: 1 }),
    ]);
    expect(out).toHaveLength(3);
  });

  it('is a no-op on an already-distinct list and preserves order', () => {
    const recents = [
      play({ artist: 'A', album: 'One', ts: 3 }),
      play({ artist: 'B', album: 'Two', ts: 2 }),
    ];
    expect(dedupeRecentsByAlbum(recents)).toEqual(recents);
  });
});
