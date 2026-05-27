import { describe, expect, it } from 'vitest';
import { useStore } from './useStore';
import type { NowPlaying } from '@/types';

function _np(overrides: Partial<NowPlaying> = {}): NowPlaying {
  return {
    ts: '2026-05-16T12:00:00Z',
    state: 'PLAYING',
    source: 'vinyl',
    release_id: 100,
    track_position: 'A1',
    ...overrides,
  };
}

describe('clearGuess action', () => {
  it('is a no-op when payload is null', () => {
    useStore.setState({ payload: null });
    useStore.getState().clearGuess();
    expect(useStore.getState().payload).toBeNull();
  });

  it('is a no-op when payload has no guess', () => {
    const payload = _np();
    useStore.setState({ payload });
    useStore.getState().clearGuess();
    expect(useStore.getState().payload).toBe(payload); // identity preserved
  });

  it('removes the guess field while preserving other payload fields', () => {
    const payload = _np({
      guess: {
        position: 'B3',
        title: 'X',
        confidence: 'high',
        source: 'llm',
      },
    });
    useStore.setState({ payload });
    useStore.getState().clearGuess();
    const next = useStore.getState().payload!;
    expect(next.guess).toBeUndefined();
    expect(next.release_id).toBe(100);
    expect(next.track_position).toBe('A1');
  });
});

describe('pinErrorReason slice', () => {
  it('starts null and can be set / cleared via setPinErrorReason', () => {
    useStore.setState({ pinErrorReason: null });
    expect(useStore.getState().pinErrorReason).toBeNull();
    useStore.getState().setPinErrorReason('bad-request');
    expect(useStore.getState().pinErrorReason).toBe('bad-request');
    useStore.getState().setPinErrorReason(null);
    expect(useStore.getState().pinErrorReason).toBeNull();
  });
});
