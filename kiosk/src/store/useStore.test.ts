import { describe, expect, it } from 'vitest';
import { albumContextKey, useStore } from './useStore';

describe('albumContextKey', () => {
  it('prefers release_id when provided', () => {
    expect(albumContextKey({ releaseId: 42 })).toBe('rid:42');
  });

  it('falls back to artist|album when release_id is absent', () => {
    expect(albumContextKey({ artist: 'Boards of Canada', album: 'Geogaddi' }))
      .toBe('name:Boards of Canada|Geogaddi');
  });

  it('prefers release_id even when artist/album also provided', () => {
    expect(albumContextKey({ releaseId: 7, artist: 'X', album: 'Y' })).toBe('rid:7');
  });

  it('returns null when neither release_id nor full artist+album available', () => {
    expect(albumContextKey({})).toBeNull();
    expect(albumContextKey({ artist: 'X' })).toBeNull();
    expect(albumContextKey({ album: 'Y' })).toBeNull();
  });

  it('treats releaseId 0 as a valid id (not falsy fallthrough)', () => {
    expect(albumContextKey({ releaseId: 0 })).toBe('rid:0');
  });
});

describe('pulseLearningChip', () => {
  it('starts at 0 and increments on each call', () => {
    // Reset by setting state directly — useStore is a singleton.
    useStore.setState({ learningChipPulses: 0 });
    expect(useStore.getState().learningChipPulses).toBe(0);
    useStore.getState().pulseLearningChip();
    expect(useStore.getState().learningChipPulses).toBe(1);
    useStore.getState().pulseLearningChip();
    useStore.getState().pulseLearningChip();
    expect(useStore.getState().learningChipPulses).toBe(3);
  });
});
