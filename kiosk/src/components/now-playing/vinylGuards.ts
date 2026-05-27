/**
 * Predicate helpers for vinyl-only affordance gates.
 *
 * Non-vinyl sources (airplay, streaming) use Sonos metadata which is
 * authoritative — the user can never have a "wrong track" on those sources.
 * Correction affordances (GuessConfirmCard, LearningChip) only make sense
 * for vinyl.
 */
import type { IdentifyState } from '@/types';

/**
 * Returns true when the source is vinyl AND the kiosk is awaiting a user
 * confirmation tap (i.e., a guess is active and requires confirmation).
 * False for all non-vinyl sources.
 */
export function isVinylAwaitingConfirm(
  identifyState: IdentifyState,
  source: string | undefined,
): boolean {
  return source === 'vinyl' && identifyState === 'awaiting-confirm';
}
