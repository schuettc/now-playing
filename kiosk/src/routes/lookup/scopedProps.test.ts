import { describe, expect, it } from 'vitest';
import type { NowPlaying, TracklistItem } from '@/types';
import { pickScopedProps } from './scopedProps';

const tracks: TracklistItem[] = [
  { position: 'A1', side: 'A', title: 'One', duration_seconds: 180 },
  { position: 'A2', side: 'A', title: 'Two', duration_seconds: 200 },
];

const base: NowPlaying = {
  ts: '2026-01-01T00:00:00Z',
  state: 'PLAYING',
  source: 'vinyl',
};

function payloadFor(release_id: number): NowPlaying {
  return {
    ...base,
    release_id,
    track_position: 'A1',
    tracklist: tracks,
    guess: { position: 'A2', title: 'Two', confidence: 'low', source: 'heuristic' },
  };
}

describe('pickScopedProps', () => {
  it('uses payload tracks and exposes highlights when release matches', () => {
    const result = pickScopedProps(42, payloadFor(42), null, false);
    expect(result).toEqual({
      releaseId: 42,
      tracks,
      payloadReleaseId: 42,
      currentPosition: 'A1',
      guessPosition: 'A2',
    });
  });

  it('falls back to apiTracks when release_id differs', () => {
    const fallback: TracklistItem[] = [
      { position: 'B1', side: 'B', title: 'Other', duration_seconds: 120 },
    ];
    const result = pickScopedProps(99, payloadFor(42), fallback, false);
    expect(result.tracks).toEqual(fallback);
    expect(result.payloadReleaseId).toBe(42);
    // Different release: current/guess stripped to avoid bleed.
    expect(result.currentPosition).toBeNull();
    expect(result.guessPosition).toBeNull();
  });

  it('returns null positions when payload is null', () => {
    const result = pickScopedProps(42, null, null, false);
    expect(result).toEqual({
      releaseId: 42,
      tracks: null,
      payloadReleaseId: undefined,
      currentPosition: null,
      guessPosition: null,
    });
  });

  it('strips currentPosition and guessPosition when fromNeedsId is true', () => {
    const result = pickScopedProps(42, payloadFor(42), null, true);
    expect(result.tracks).toEqual(tracks);
    expect(result.payloadReleaseId).toBe(42);
    expect(result.currentPosition).toBeNull();
    expect(result.guessPosition).toBeNull();
  });

  it('preserves payloadReleaseId under fromNeedsId so album-changed guard still works', () => {
    // Album-changed guard in LookupViewScoped depends on payloadReleaseId
    // transitioning from matching → mismatching. Stripping the highlights
    // must not break that signal.
    const result = pickScopedProps(42, payloadFor(42), null, true);
    expect(result.payloadReleaseId).toBe(42);
  });

  it('non-needs-id flow is unchanged from prior behaviour', () => {
    const a = pickScopedProps(42, payloadFor(42), null, false);
    const b = pickScopedProps(42, payloadFor(42), null, false);
    expect(a).toEqual(b);
    expect(a.currentPosition).toBe('A1');
    expect(a.guessPosition).toBe('A2');
  });
});
