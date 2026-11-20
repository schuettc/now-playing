import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'wouter';
import type { Guess } from '@/types';
import { useStore } from '@/store/useStore';
import { usePinTrack } from '@/hooks/usePinTrack';
import { MOTION } from '@/lib/motion';
import { track as telemetryTrack } from '@/lib/telemetry';

export type GuessVariant = 'card';

export interface GuessConfirmHandlers {
  onConfirm: () => void;
  onPickAnother: () => void;
  /** How long the card lives, fixed when it mounts; the strip drains over it. */
  lifetimeMs: number;
}

/**
 * The card's lifetime: the guessed track's remaining time as the backend
 * reports it, or the fixed window when the backend has no duration.
 */
export function guessLifetimeMs(guess: Pick<Guess, 'expires_in_s'>): number {
  return guess.expires_in_s != null
    ? guess.expires_in_s * 1000
    : MOTION.guessTimeoutMs;
}

/**
 * Start the card's lifetime clock. When it runs out the card is cleared
 * locally and remembered as run out, so it does not come back for the
 * same track. Nothing is posted: a card nobody answered is not a
 * rejection. Returns the cancel function.
 */
export function scheduleGuessExpiry(
  lifetimeMs: number,
  latestGuess: () => Pick<Guess, 'confidence' | 'source'>,
  variant: GuessVariant,
): () => void {
  const t = setTimeout(() => {
    const { confidence, source } = latestGuess();
    telemetryTrack('identify_guess_timeout', { variant, confidence, source });
    useStore.getState().expireGuess();
  }, lifetimeMs);
  return () => clearTimeout(t);
}

/**
 * Tap handlers and the single lifetime clock for `GuessConfirmCard`.
 *
 * Reads `payload.release_id` dynamically on each tap so a mid-prompt
 * record swap doesn't pin a track on the wrong album. When the lifetime
 * runs out the card is cleared locally (see `scheduleGuessExpiry`).
 */
export function useGuessConfirmHandlers(
  guess: Guess,
  variant: GuessVariant,
): GuessConfirmHandlers {
  const [, navigate] = useLocation();
  const pinTrack = usePinTrack();

  // Fire `identify_guess_shown` once per *unique guess identity*.
  // The WS payload re-references `guess` on every heartbeat (~5s),
  // so the object identity is unstable. Stringifying the stable
  // primitives keeps this from re-firing on every heartbeat while
  // still firing again when the backend changes its mind.
  // (impl-review-1 blocker fix.)
  useEffect(() => {
    telemetryTrack('identify_guess_shown', {
      variant,
      confidence: guess.confidence,
      source: guess.source,
      has_alt: Boolean(guess.alt),
    });
  }, [guess.position, guess.title, guess.confidence, guess.source, variant]);

  const mountedAtRef = useRef(Date.now());
  const msToDecide = () => Date.now() - mountedAtRef.current;

  // The card is keyed by guess position, so this is captured once per
  // guess. Later heartbeats restamp `expires_in_s` and `confidence` on the
  // same guess; they must not restart the clock.
  const [lifetimeMs] = useState(() => guessLifetimeMs(guess));
  const latestGuess = useRef(guess);
  latestGuess.current = guess;

  // Lifetime over: clear locally + telemetry.
  useEffect(
    () => scheduleGuessExpiry(lifetimeMs, () => latestGuess.current, variant),
    [lifetimeMs, variant],
  );

  const currentReleaseId = (): number | null | undefined =>
    useStore.getState().payload?.release_id;

  const onConfirm = () => {
    const rid = currentReleaseId();
    if (typeof rid !== 'number') return;
    telemetryTrack('identify_guess_confirm', {
      variant,
      confidence: guess.confidence,
      source: guess.source,
      alt_picked: false,
      ms_to_decide: msToDecide(),
    });
    void pinTrack({ release_id: rid, track_position: guess.position, entry: 'guess' });
  };

  const onPickAnother = () => {
    telemetryTrack('identify_guess_pick_another', {
      variant,
      confidence: guess.confidence,
      source: guess.source,
      ms_to_decide: msToDecide(),
    });
    navigate('/identify?scope=track');
  };

  return { onConfirm, onPickAnother, lifetimeMs };
}
