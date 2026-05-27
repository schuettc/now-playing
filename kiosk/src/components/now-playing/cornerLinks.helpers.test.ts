import { describe, expect, it } from 'vitest';
import type { NowPlaying } from '@/types';
import { buildCornerLinks } from './cornerLinks.helpers';

function np(overrides: Partial<NowPlaying> = {}): NowPlaying {
  return {
    state: 'playing',
    source: 'streaming',
    ...overrides,
  } as NowPlaying;
}

// Wrong-track / wrong-album links retired by
// `identify-learning-chip-undo-strip` — UndoStrip is now the single
// affordance. These tests cover the surviving Discogs link only.

describe('buildCornerLinks', () => {
  it('hides discogs link when track is not being shown', () => {
    const v = buildCornerLinks({
      data: np({ release_id: 123 }),
      showTrack: false,
      showNeedsId: false,
      showVinylIdentifying: false,
    });
    expect(v.discogsReleaseId).toBeNull();
  });

  it('hides discogs link when release_id missing', () => {
    const v = buildCornerLinks({
      data: np(),
      showTrack: true,
      showNeedsId: false,
      showVinylIdentifying: false,
    });
    expect(v.discogsReleaseId).toBeNull();
  });

  it('exposes discogs release id when locked', () => {
    const v = buildCornerLinks({
      data: np({ release_id: 42 }),
      showTrack: true,
      showNeedsId: false,
      showVinylIdentifying: false,
    });
    expect(v.discogsReleaseId).toBe(42);
  });

  it('handles null data without throwing', () => {
    const v = buildCornerLinks({
      data: null,
      showTrack: true,
      showNeedsId: false,
      showVinylIdentifying: false,
    });
    expect(v.discogsReleaseId).toBeNull();
  });
});
