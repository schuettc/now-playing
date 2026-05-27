import { describe, it, expect } from 'vitest';
import { getSidePrefix, joinCredits } from './trackInfoHelpers';

describe('getSidePrefix', () => {
  it('returns track_position when source is vinyl', () => {
    expect(getSidePrefix({ source: 'vinyl', track_position: 'A1' })).toBe('A1');
  });

  it('returns null when source is vinyl but no track_position', () => {
    expect(getSidePrefix({ source: 'vinyl', track_position: undefined })).toBeNull();
    expect(getSidePrefix({ source: 'vinyl', track_position: '' })).toBeNull();
  });

  it('returns null when source is not vinyl, even with track_position', () => {
    expect(getSidePrefix({ source: 'airplay', track_position: 'A1' })).toBeNull();
    expect(getSidePrefix({ source: 'streaming', track_position: 'B2' })).toBeNull();
  });
});

describe('joinCredits', () => {
  it('returns null when both empty', () => {
    expect(joinCredits(undefined, undefined)).toBeNull();
    expect(joinCredits('', '')).toBeNull();
  });

  it('returns single value when only one provided', () => {
    expect(joinCredits('Blue Note', undefined)).toBe('Blue Note');
    expect(joinCredits(undefined, 'BN-1577')).toBe('BN-1577');
  });

  it('joins with " · " separator', () => {
    expect(joinCredits('Blue Note', 'BN-1577')).toBe('Blue Note · BN-1577');
  });
});
