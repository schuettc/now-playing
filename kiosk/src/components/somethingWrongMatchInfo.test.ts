import { describe, it, expect } from 'vitest';
import { formatRecognitionAgo, buildMatchInfo } from './somethingWrongMatchInfo';

const T0 = 1_000_000_000_000;

describe('formatRecognitionAgo', () => {
  it('returns null when the timestamp is null (no recognition yet)', () => {
    expect(formatRecognitionAgo(null, T0)).toBe(null);
  });

  it('reads "just now" for deltas under 5 seconds', () => {
    expect(formatRecognitionAgo(T0, T0)).toBe('just now');
    expect(formatRecognitionAgo(T0, T0 + 4_000)).toBe('just now');
  });

  it('formats sub-minute deltas as `Xs ago`', () => {
    expect(formatRecognitionAgo(T0, T0 + 12_000)).toBe('12s ago');
    expect(formatRecognitionAgo(T0, T0 + 59_000)).toBe('59s ago');
  });

  it('formats sub-hour deltas as `Xm ago`', () => {
    expect(formatRecognitionAgo(T0, T0 + 60_000)).toBe('1m ago');
    expect(formatRecognitionAgo(T0, T0 + 59 * 60_000)).toBe('59m ago');
  });

  it('formats >= 1h deltas as `Xh ago`', () => {
    expect(formatRecognitionAgo(T0, T0 + 60 * 60_000)).toBe('1h ago');
    expect(formatRecognitionAgo(T0, T0 + 2 * 60 * 60_000 + 30_000)).toBe('2h ago');
  });

  it('clamps negative deltas (clock skew) to `just now`', () => {
    expect(formatRecognitionAgo(T0 + 5_000, T0)).toBe('just now');
  });
});

describe('buildMatchInfo', () => {
  it('combines method label + relative time', () => {
    const info = buildMatchInfo('vinyl', 'confirmed-shazam', T0, T0 + 12_000);
    expect(info.method).toBe('Shazam · matched');
    expect(info.agoLabel).toBe('12s ago');
  });

  it('returns empty agoLabel when timestamp is null', () => {
    const info = buildMatchInfo('vinyl', 'confirmed-shazam', null, T0);
    expect(info.agoLabel).toBe('');
  });

  it('falls back to source label for non-vinyl (no method sub)', () => {
    const info = buildMatchInfo('streaming', 'confirmed-shazam', T0, T0 + 1_000);
    // Streaming suppresses the method sub; method label falls back to "STREAMING".
    expect(info.method).toBe('STREAMING');
  });
});
