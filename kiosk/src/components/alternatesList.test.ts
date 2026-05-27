import { describe, it, expect } from 'vitest';
import { alternateMetaLine } from './AlternatesList';

describe('alternateMetaLine', () => {
  it('joins position, year, and format with bullets', () => {
    expect(
      alternateMetaLine({
        release_id: 1,
        album: 'X',
        track_position: 'B3',
        year: 2018,
        format: 'LP',
      }),
    ).toBe('B3 · 2018 · LP');
  });

  it('omits missing fields and skips their separators', () => {
    expect(alternateMetaLine({ release_id: 1, album: 'X', year: 2018 })).toBe('2018');
    expect(
      alternateMetaLine({ release_id: 1, album: 'X', track_position: 'B3' }),
    ).toBe('B3');
  });

  it('returns empty string when nothing to show', () => {
    expect(alternateMetaLine({ release_id: 1, album: 'X' })).toBe('');
  });

  it('treats year=0 as a real value (defensive)', () => {
    expect(alternateMetaLine({ release_id: 1, album: 'X', year: 0 })).toBe('0');
  });
});
