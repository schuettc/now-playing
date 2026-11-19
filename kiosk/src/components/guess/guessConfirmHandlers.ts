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

async function postDismissGuess(release_id: number, track_position: string): Promise<void> {
  try {
    await fetch('/api/dismiss-guess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id, track_position }),
    });
  } catch {
    // Best-effort; the optimistic clearGuess() already removed the
    // prompt locally. If the backend never gets the signal it'll
    // re-emit the guess on the next heartbeat, and the user can
    // dismiss again. Not worth blocking the UX over a network blip.
  }
}

/**
 * Tap handlers and the single lifetime clock for `GuessConfirmCard`.
 *
 * Reads `payload.release_id` dynamically on each tap so a mid-prompt
 * record swap doesn't send `/api/dismiss-guess` to the wrong album.
 * When the lifetime runs out it fires both the local `clearGuess()` and
 * `POST /api/dismiss-guess` so the backend stops emitting the same
 * guess afterward.
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

  // Lifetime over: clear locally + POST dismiss + telemetry.
  useEffect(() => {
    const t = setTimeout(() => {
      const { position, confidence, source } = latestGuess.current;
      const rid = useStore.getState().payload?.release_id;
      telemetryTrack('identify_guess_timeout', {
        variant,
        confidence,
        source,
      });
      if (typeof rid === 'number') {
        void postDismissGuess(rid, position);
      }
      useStore.getState().clearGuess();
    }, lifetimeMs);
    return () => clearTimeout(t);
  }, [lifetimeMs, variant]);

  const currentReleaseId = (): number | undefined =>
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
