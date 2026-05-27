import { describe, it, expect } from 'vitest';
import { primaryButtonLabel, pickManuallyLabel } from './guessCardCopy';
import type { Guess } from '@/types';

const baseGuess: Guess = {
  position: 'B6',
  title: 'Blank',
  confidence: 'medium',
  source: 'heuristic',
};

describe('guessCardCopy', () => {
  it('primaryButtonLabel bakes the title into the CTA', () => {
    expect(primaryButtonLabel(baseGuess)).toBe("Yes, that's Blank");
  });

  it('primaryButtonLabel handles unusual punctuation in title', () => {
    const g = { ...baseGuess, title: "Don't Look Back" };
    expect(primaryButtonLabel(g)).toBe("Yes, that's Don't Look Back");
  });

  it('primaryButtonLabel falls back when title is empty', () => {
    const g = { ...baseGuess, title: '' };
    expect(primaryButtonLabel(g)).toBe("Yes, that's it");
  });

  it('pickManuallyLabel returns the canonical ghost-button text', () => {
    expect(pickManuallyLabel()).toBe('Pick a track manually →');
  });
});
