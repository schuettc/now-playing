import { describe, it, expect } from 'vitest';
import { clearFingerprintsCopy } from './ClearFingerprintsConfirm';
import type { NowPlaying } from '@/types';

function base(over: Partial<NowPlaying> = {}): NowPlaying {
  return { source: 'vinyl', ...over } as NowPlaying;
}

describe('clearFingerprintsCopy', () => {
  it('uses the title and parenthesized position when both are present', () => {
    const copy = clearFingerprintsCopy(
      base({ title: 'Lucky Time', track_position: 'C3' }),
      7,
      false,
    );
    expect(copy.title).toBe('Lucky Time');
    expect(copy.position).toBe(' (C3)');
  });

  it('falls back to "this track" when title is missing', () => {
    expect(clearFingerprintsCopy(base(), 1, false).title).toBe('this track');
  });

  it('singularizes "fingerprint" for count=1', () => {
    expect(clearFingerprintsCopy(base(), 1, false).body).toContain('1 fingerprint ');
  });

  it('pluralizes "fingerprints" for counts other than 1', () => {
    expect(clearFingerprintsCopy(base(), 0, false).body).toContain('0 fingerprints');
    expect(clearFingerprintsCopy(base(), 7, false).body).toContain('7 fingerprints');
  });

  it('swaps confirmLabel while busy', () => {
    expect(clearFingerprintsCopy(base(), 1, false).confirmLabel).toBe('Forget them');
    expect(clearFingerprintsCopy(base(), 1, true).confirmLabel).toBe('Forgetting…');
  });
});
