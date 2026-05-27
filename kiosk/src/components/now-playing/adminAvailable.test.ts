import { describe, expect, it } from 'vitest';
import type { NowPlaying } from '@/types';
import { computeAdminAvailable } from './adminAvailable';

const base: NowPlaying = {
  ts: '2024-01-01T00:00:00Z',
  source: 'streaming',
  state: 'PLAYING',
};

describe('computeAdminAvailable', () => {
  it('returns false when no data', () => {
    expect(computeAdminAvailable(null)).toBe(false);
  });

  it('returns false when no title', () => {
    expect(computeAdminAvailable({ ...base, album: 'A' })).toBe(false);
  });

  it('returns true for vinyl with title', () => {
    expect(
      computeAdminAvailable({ ...base, source: 'vinyl', title: 'T' }),
    ).toBe(true);
  });

  it('returns true when release_id is set', () => {
    expect(
      computeAdminAvailable({ ...base, title: 'T', release_id: 5 }),
    ).toBe(true);
  });

  it('returns true when album is set', () => {
    expect(computeAdminAvailable({ ...base, title: 'T', album: 'A' })).toBe(
      true,
    );
  });

  it('returns false for non-vinyl title-only (no album, no release_id)', () => {
    expect(computeAdminAvailable({ ...base, title: 'T' })).toBe(false);
  });
});
