import { describe, expect, it } from 'vitest';
import { currentTrackKey } from './TracklistPanel';

describe('currentTrackKey', () => {
  it('returns null when there is no current position', () => {
    expect(currentTrackKey(123, null)).toBeNull();
    expect(currentTrackKey(123, undefined)).toBeNull();
    expect(currentTrackKey(123, '')).toBeNull();
  });

  it('combines releaseId and position into a stable key', () => {
    expect(currentTrackKey(42, 'B9')).toBe('42::B9');
  });

  it('produces the same key for identical (releaseId, position) inputs', () => {
    // Redundant WS publishes that re-emit the same track must not
    // trigger a scroll. Identity-key equality is what guards that.
    expect(currentTrackKey(42, 'B9')).toBe(currentTrackKey(42, 'B9'));
  });

  it('differs when the track position changes within a release', () => {
    expect(currentTrackKey(42, 'B9')).not.toBe(currentTrackKey(42, 'B10'));
  });

  it('differs when the release changes (e.g. after manual identify)', () => {
    expect(currentTrackKey(42, 'A1')).not.toBe(currentTrackKey(99, 'A1'));
  });

  it('uses a placeholder release segment when releaseId is missing', () => {
    // Pre-lock or non-vinyl source — position alone should still be a
    // stable key so scroll fires once per position change.
    expect(currentTrackKey(undefined, 'A1')).toBe('r::A1');
    expect(currentTrackKey(undefined, 'A1')).toBe(currentTrackKey(undefined, 'A1'));
  });
});
