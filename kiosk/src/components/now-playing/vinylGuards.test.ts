/**
 * Tests for vinyl-only affordance gate helpers.
 *
 * These guards ensure that correction affordances (GuessConfirmCard,
 * LearningChip) are suppressed for non-vinyl sources where Sonos metadata
 * is authoritative. See docs/features/streaming-protected-from-vinyl-cascade/.
 */
import { describe, expect, it } from 'vitest';
import { isVinylAwaitingConfirm } from './vinylGuards';

describe('isVinylAwaitingConfirm', () => {
  it('returns true for vinyl + awaiting-confirm', () => {
    expect(isVinylAwaitingConfirm('awaiting-confirm', 'vinyl')).toBe(true);
  });

  it('returns false for airplay + awaiting-confirm', () => {
    // GuessConfirmCard must never render for airplay — Sonos metadata is authoritative.
    expect(isVinylAwaitingConfirm('awaiting-confirm', 'airplay')).toBe(false);
  });

  it('returns false for streaming + awaiting-confirm', () => {
    expect(isVinylAwaitingConfirm('awaiting-confirm', 'streaming')).toBe(false);
  });

  it('returns false for vinyl + confirmed-shazam (not awaiting confirm)', () => {
    expect(isVinylAwaitingConfirm('confirmed-shazam', 'vinyl')).toBe(false);
  });

  it('returns false when source is undefined', () => {
    expect(isVinylAwaitingConfirm('awaiting-confirm', undefined)).toBe(false);
  });

  it('returns false for tv and unknown sources', () => {
    expect(isVinylAwaitingConfirm('awaiting-confirm', 'tv')).toBe(false);
    expect(isVinylAwaitingConfirm('awaiting-confirm', 'unknown')).toBe(false);
  });
});
