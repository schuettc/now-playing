import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStore } from '@/store/useStore';
import type { Guess, NowPlaying } from '@/types';
import { guessLifetimeMs, scheduleGuessExpiry } from './guessConfirmHandlers';

function guessFor(position: string, title: string): Guess {
  return { position, title, confidence: 'medium', source: 'heuristic', expires_in_s: 2 };
}

function predicted(release_id: number, guess: Guess): NowPlaying {
  return {
    ts: '2026-11-20T16:01:07Z',
    state: 'PLAYING',
    source: 'vinyl',
    release_id,
    track_position: guess.position,
    title: guess.title,
    guess,
  };
}

const BLANK = guessFor('B6', 'Blank');

/** Show the card for `guess` and let its lifetime run out. */
function showAndExpire(guess: Guess): void {
  useStore.getState().setPayload(predicted(31427573, guess));
  scheduleGuessExpiry(guessLifetimeMs(guess), () => guess, 'card');
  vi.advanceTimersByTime(2000);
}

describe('the Best guess card running out', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock = vi.fn(() => Promise.resolve(new Response('{}')));
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'log').mockImplementation(() => {});
    useStore.setState({ payload: null, expiredGuess: null });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('does not post a dismissal when the lifetime runs out', () => {
    useStore.getState().setPayload(predicted(31427573, BLANK));
    scheduleGuessExpiry(guessLifetimeMs(BLANK), () => BLANK, 'card');

    vi.advanceTimersByTime(1999);
    expect(useStore.getState().payload?.guess).toBeDefined();
    vi.advanceTimersByTime(1);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useStore.getState().payload?.guess).toBeUndefined();
    expect(console.log).toHaveBeenCalledWith(
      '[telemetry]',
      'identify_guess_timeout',
      expect.objectContaining({ variant: 'card' }),
    );
  });

  it('does not run out once cancelled', () => {
    useStore.getState().setPayload(predicted(31427573, BLANK));
    const cancel = scheduleGuessExpiry(guessLifetimeMs(BLANK), () => BLANK, 'card');
    cancel();
    vi.advanceTimersByTime(5000);
    expect(useStore.getState().payload?.guess).toBeDefined();
  });

  it('does not show the card again for the track that ran out', () => {
    showAndExpire(BLANK);

    useStore.getState().setPayload(predicted(31427573, { ...BLANK, expires_in_s: 200 }));

    const payload = useStore.getState().payload;
    expect(payload?.guess).toBeUndefined();
    expect(payload?.title).toBe('Blank');
  });

  it('shows the card for the next track', () => {
    showAndExpire(BLANK);

    const next = guessFor('B7', 'Segue 2');
    useStore.getState().setPayload(predicted(31427573, next));

    expect(useStore.getState().payload?.guess).toEqual(next);
    // The memory is dropped: the same position guessed again later shows.
    useStore.getState().setPayload(predicted(31427573, BLANK));
    expect(useStore.getState().payload?.guess).toEqual(BLANK);
  });

  it('shows the card again when the track is played again later', () => {
    showAndExpire(BLANK);

    // The next track is confirmed by Shazam: no guess on its payload.
    useStore.getState().setPayload({
      ts: '2026-11-20T16:06:49Z',
      state: 'PLAYING',
      source: 'vinyl',
      release_id: 31427573,
      track_position: 'B7',
      title: 'Segue 2',
    });
    expect(useStore.getState().expiredGuess).toBeNull();

    useStore.getState().setPayload(predicted(31427573, BLANK));
    expect(useStore.getState().payload?.guess).toEqual(BLANK);
  });

  it('shows the card again after the screen went idle', () => {
    showAndExpire(BLANK);

    useStore.getState().setPayload({
      ts: '2026-11-20T16:20:00Z',
      state: 'STOPPED',
      source: 'vinyl',
    });
    useStore.getState().setPayload(predicted(31427573, BLANK));

    expect(useStore.getState().payload?.guess).toEqual(BLANK);
  });

  it('shows the card for the same position on another release', () => {
    showAndExpire(BLANK);

    useStore.getState().setPayload(predicted(999, BLANK));

    expect(useStore.getState().payload?.guess).toEqual(BLANK);
  });
});
