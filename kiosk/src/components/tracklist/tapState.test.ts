import { describe, expect, it } from 'vitest';
import { rowState, shouldUseTappable } from './tapState';
import type { NowPlaying } from '@/types';

describe('rowState', () => {
  it('returns current when position matches currentPosition', () => {
    expect(
      rowState({ position: 'A1', currentPosition: 'A1', guessPosition: null }),
    ).toBe('current');
  });

  it('returns guess when position matches guessPosition (no current match)', () => {
    expect(
      rowState({ position: 'A2', currentPosition: 'A1', guessPosition: 'A2' }),
    ).toBe('guess');
  });

  it('current wins over guess on the same row', () => {
    expect(
      rowState({ position: 'A1', currentPosition: 'A1', guessPosition: 'A1' }),
    ).toBe('current');
  });

  it('returns default when position matches neither', () => {
    expect(
      rowState({ position: 'B3', currentPosition: 'A1', guessPosition: 'A2' }),
    ).toBe('default');
  });

  it('returns default when current/guess are null', () => {
    expect(
      rowState({ position: 'A1', currentPosition: null, guessPosition: null }),
    ).toBe('default');
    expect(
      rowState({ position: 'A1', currentPosition: undefined, guessPosition: undefined }),
    ).toBe('default');
  });
});

describe('shouldUseTappable', () => {
  const np = (overrides: Partial<NowPlaying> = {}): NowPlaying => ({
    ts: '2026-05-16T12:00:00Z',
    state: 'PLAYING',
    source: 'vinyl',
    release_id: 100,
    ...overrides,
  });

  it('returns true for vinyl + release_id', () => {
    expect(shouldUseTappable(np())).toBe(true);
  });

  it('returns false for vinyl without release_id', () => {
    expect(shouldUseTappable(np({ release_id: undefined }))).toBe(false);
  });

  it('returns false for streaming source even with release_id', () => {
    expect(shouldUseTappable(np({ source: 'streaming' }))).toBe(false);
  });

  it('returns false for airplay source', () => {
    expect(shouldUseTappable(np({ source: 'airplay' }))).toBe(false);
  });

  it('returns false for null / undefined payload', () => {
    expect(shouldUseTappable(null)).toBe(false);
    expect(shouldUseTappable(undefined)).toBe(false);
  });
});
