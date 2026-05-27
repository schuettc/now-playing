import { describe, expect, it } from 'vitest';
import { buildCaptionParts } from './albumCardHelpers';
import type { SearchRelease } from './types';

const base: SearchRelease = {
  release_id: 1,
  artist: 'A',
  title: 'T',
  tracks: [],
};

describe('buildCaptionParts', () => {
  it('includes year, label, and catno when present', () => {
    expect(
      buildCaptionParts({ ...base, year: 1999, label: 'Sub Pop', catno: 'SP123' }),
    ).toEqual(['1999', 'Sub Pop', 'SP123']);
  });

  it('omits missing pieces and stringifies year', () => {
    expect(buildCaptionParts({ ...base, year: 2001 })).toEqual(['2001']);
    expect(buildCaptionParts({ ...base, label: 'X' })).toEqual(['X']);
    expect(buildCaptionParts(base)).toEqual([]);
  });

  it('treats empty strings as missing', () => {
    expect(
      buildCaptionParts({ ...base, label: '', catno: '', year: undefined }),
    ).toEqual([]);
  });
});
