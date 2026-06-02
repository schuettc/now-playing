import { describe, expect, it } from 'vitest';
import { deriveIdentifyState } from './useIdentifyState';
// pinPosition exposure tested indirectly via TappableTrackRow's
// integration; the hook lacks render-test infrastructure
// (@testing-library/react not installed).
import { MOTION } from '@/lib/motion';
import type { NowPlaying } from '@/types';

function pl(overrides: Partial<NowPlaying> = {}): NowPlaying {
  return {
    ts: '2026-05-16T12:00:00Z',
    state: 'PLAYING',
    source: 'vinyl',
    release_id: 100,
    track_position: 'A1',
    ...overrides,
  };
}

describe('deriveIdentifyState', () => {
  it('returns needs-id for null payload with no identifying timer', () => {
    expect(deriveIdentifyState(null, null, null, null, 0, null)).toBe('needs-id');
  });

  // --- cold-start / null-payload + timer ---

  it('returns identifying for null payload when timer is active (cold-start WS gap)', () => {
    // Core cold-start fix: WS not yet connected / between reconnect frames.
    // If the timer was previously armed (vinyl session started before the gap),
    // show the soft "identifying" UI rather than the loud "Unknown" failure screen.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(null, null, null, null, t + 1_000, t),
    ).toBe('identifying');
  });

  it('returns needs-id for null payload when identifying timer has elapsed', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(null, null, null, null, t + MOTION.identifyingTimeoutMs + 1, t),
    ).toBe('needs-id');
  });

  it('returns needs-id for null payload when timer elapsed exactly at boundary', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(null, null, null, null, t + MOTION.identifyingTimeoutMs, t),
    ).toBe('needs-id');
  });

  it('returns confirmed-shazam for shazam match_method', () => {
    expect(
      deriveIdentifyState(pl({ match_method: 'shazam' }), null, null, null, 0, null),
    ).toBe('confirmed-shazam');
  });

  it('returns confirmed-shazam for sonos-didl and sonos-polled', () => {
    expect(
      deriveIdentifyState(pl({ match_method: 'sonos-didl' }), null, null, null, 0, null),
    ).toBe('confirmed-shazam');
    expect(
      deriveIdentifyState(pl({ match_method: 'sonos-polled' }), null, null, null, 0, null),
    ).toBe('confirmed-shazam');
  });

  it('returns confirmed-local for fingerprint match_method', () => {
    expect(
      deriveIdentifyState(pl({ match_method: 'fingerprint' }), null, null, null, 0, null),
    ).toBe('confirmed-local');
  });

  it('returns confirmed-local for user-identified and user-selected (post-pin graduation)', () => {
    expect(
      deriveIdentifyState(pl({ match_method: 'user-identified' }), null, null, null, 0, null),
    ).toBe('confirmed-local');
    expect(
      deriveIdentifyState(pl({ match_method: 'user-selected' }), null, null, null, 0, null),
    ).toBe('confirmed-local');
  });

  it('pin HOLDS at user-pinned when backend acknowledges with user-identified', () => {
    // Critical UX: backend acks the pin (sets match_method=user-identified
    // within ~200ms). user-pinned must NOT graduate early — the 50s
    // violet "learning" bridge is what we're protecting.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'user-identified', track_position: 'A1' }),
        t,
        100,
        'A1',
        t + 1_000,
        null,
      ),
    ).toBe('user-pinned');
  });

  it('post-graduation holds at confirmed-local even with no match_method', () => {
    // After 50s, the kiosk should NOT revert to needs-id if Feature A
    // hasn't published a fingerprint match yet. Trust the pin.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: undefined, release_id: 100 }),
        t,
        100,
        'A1',
        t + 60_000,
        null,
      ),
    ).toBe('confirmed-local');
  });

  it('post-graduation upgrades to confirmed-shazam when shazam lands', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'shazam' }),
        t,
        100,
        'A1',
        t + 60_000,
        null,
      ),
    ).toBe('confirmed-shazam');
  });

  it('post-graduation falls through when payload moves to a different track', () => {
    // User pinned A1; 50s later the kiosk is showing A2 (real track
    // progression, not a flaky Shazam). Pin trust should NOT
    // override the new payload's standard derivation. impl-review-3.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched', track_position: 'A2' }),
        t,
        100,
        'A1',
        t + 60_000,
        null,
      ),
    ).toBe('needs-id');
  });

  it('returns awaiting-confirm when guess present and no pin', () => {
    expect(
      deriveIdentifyState(
        pl({
          guess: {
            position: 'A1',
            title: 'Pitiful',
            confidence: 'high',
            source: 'heuristic',
          },
        }),
        null,
        null,
        null,
        0,
        null,
      ),
    ).toBe('awaiting-confirm');
  });

  it('does NOT prompt confirm when the backend marks the guess not confirmable', () => {
    // Backend contract (epic consolidate-guess-confidence-lifetime): a guess
    // attached to a confirmed now-playing has confirmable:false → no card.
    expect(
      deriveIdentifyState(
        pl({
          guess: {
            position: 'A1',
            title: 'Pitiful',
            confidence: 'high',
            source: 'window',
            confirmable: false,
          },
        }),
        null,
        null,
        null,
        MOTION.identifyingTimeoutMs + 1,
        0,
      ),
    ).toBe('needs-id');
  });

  it('returns needs-id when no match and no guess (no identifying timer)', () => {
    expect(deriveIdentifyState(pl({ match_method: 'unmatched' }), null, null, null, 0, null))
      .toBe('needs-id');
  });

  it('returns user-pinned during the 50s window', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(pl({ match_method: 'unmatched' }), t, 100, 'A1', t + 1_000, null),
    ).toBe('user-pinned');
  });

  it('graduates from user-pinned to confirmed-local after pinGraduationMs elapses', () => {
    // Per impl-review-2 fix: never revert to needs-id while a valid
    // pin remains on this release. Feature A is expected to have
    // written refs by the time the 50s window closes.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched' }),
        t,
        100,
        'A1',
        t + MOTION.pinGraduationMs + 1,
        null,
      ),
    ).toBe('confirmed-local');
  });

  it('honors pin when confirmed match arrives for SAME track_position', () => {
    const t = 1_000_000;
    // Mid-pin, backend caught up with a shazam match for the same pos.
    expect(
      deriveIdentifyState(
        pl({ match_method: 'shazam', track_position: 'A1' }),
        t,
        100,
        'A1',
        t + 1_000,
        null,
      ),
    ).toBe('confirmed-shazam');
    expect(
      deriveIdentifyState(
        pl({ match_method: 'fingerprint', track_position: 'A1' }),
        t,
        100,
        'A1',
        t + 1_000,
        null,
      ),
    ).toBe('confirmed-local');
  });

  it('pin holds when confirmed match arrives for DIFFERENT track_position', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'shazam', track_position: 'A2' }),
        t,
        100,
        'A1',
        t + 1_000,
        null,
      ),
    ).toBe('user-pinned');
  });

  it('invalidates pin overlay on album change (release_id mismatch)', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ release_id: 200, match_method: 'unmatched' }),
        t,
        100,
        'A1',
        t + 1_000,
        null,
      ),
    ).toBe('needs-id');
  });

  // --- identifying state tests ---

  it('returns identifying when no match and timer is active within 45s', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched' }),
        null,
        null,
        null,
        t + 1_000,
        t, // identifyingStartedAtMs = t, nowMs = t + 1s → within 45s
      ),
    ).toBe('identifying');
  });

  it('returns needs-id after identifyingTimeoutMs elapses', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched' }),
        null,
        null,
        null,
        t + MOTION.identifyingTimeoutMs + 1,
        t,
      ),
    ).toBe('needs-id');
  });

  it('returns identifying exactly at 44999ms (one ms before timeout)', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched' }),
        null,
        null,
        null,
        t + MOTION.identifyingTimeoutMs - 1,
        t,
      ),
    ).toBe('identifying');
  });

  it('returns needs-id exactly at identifyingTimeoutMs boundary', () => {
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched' }),
        null,
        null,
        null,
        t + MOTION.identifyingTimeoutMs,
        t,
      ),
    ).toBe('needs-id');
  });

  it('confirmed match clears identifying regardless of timer', () => {
    // If shazam lands while the timer is still active, it should take
    // precedence — confirmed-shazam, not identifying.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'shazam' }),
        null,
        null,
        null,
        t + 1_000,
        t,
      ),
    ).toBe('confirmed-shazam');
  });

  it('awaiting-confirm takes precedence over identifying timer', () => {
    // When a guess arrives while the timer is active, show awaiting-confirm
    // (user can confirm the guess), not identifying.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({
          match_method: undefined,
          guess: {
            position: 'A1',
            title: 'Pitiful',
            confidence: 'high',
            source: 'heuristic',
          },
        }),
        null,
        null,
        null,
        t + 1_000,
        t,
      ),
    ).toBe('awaiting-confirm');
  });

  it('pin set during identifying window → user-pinned (pin takes precedence)', () => {
    // User taps to confirm while the identifying timer is running.
    // user-pinned should win over identifying.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(
        pl({ match_method: 'unmatched', release_id: 100 }),
        t,       // pinAtMs
        100,     // pinReleaseId
        'A1',    // pinPosition
        t + 500, // nowMs — within pin window
        t,       // identifyingStartedAtMs — timer also active
      ),
    ).toBe('user-pinned');
  });

  it('null payload + active timer → identifying (not needs-id; see cold-start fix)', () => {
    // Replaces the pre-fix "needs-id regardless of timer" assertion.
    // With the cold-start fix, an active timer DOES gate the return value
    // even when payload is null.
    const t = 1_000_000;
    expect(
      deriveIdentifyState(null, null, null, null, t + 1_000, t),
    ).toBe('identifying');
  });
});
