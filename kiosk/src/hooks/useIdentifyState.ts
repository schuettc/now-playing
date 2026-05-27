/**
 * Derive the unified IdentifyState for the StatusPill + behavior triggers.
 *
 * Two state axes are orthogonal in this kiosk:
 *  - ScreenState (`useScreenState`) — which top-level surface to render.
 *  - IdentifyState (this hook) — confidence/source label for the pill +
 *    triggers for GuessConfirm / TappableTrackRow visual states.
 *
 * The transient `user-pinned` state is client-only: set on a local tap
 * (`pin(releaseId)`) and held for `MOTION.pinGraduationMs` (50s — matches
 * Feature A's cohort fill time of cap=10 × ~5s heartbeat). When the
 * timer elapses, derivation flips to `confirmed-local`.
 *
 * The transient `identifying` state is client-only: started automatically
 * when a vinyl source has no recognition for the first time (or a new
 * session begins). Held for `MOTION.identifyingTimeoutMs` (45s — covers
 * ~3 heartbeats, enough for blind fingerprint discovery to converge).
 * After 45s without recognition, derivation falls through to `needs-id`.
 *
 * Backend-override semantics:
 *  - A confirmed match for the SAME track_position as the pin flips
 *    immediately (system caught up early).
 *  - A confirmed match for a DIFFERENT track_position is ignored
 *    while the pin holds (user assertion outranks a contradicting
 *    auto-recognition until TTL).
 *  - An album-level change (release_id differs from the pin's) drops
 *    the pin overlay; we use payload-derived state for the new album.
 *
 * See docs/features/identify-confirm-first-ux/.
 * See docs/features/kiosk-identifying-state/.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { IdentifyState, NowPlaying } from '@/types';
import { MOTION } from '@/lib/motion';

const CONFIRMED_SHAZAM_METHODS: Array<NowPlaying['match_method']> = [
  'shazam',
  'sonos-didl',
  'sonos-polled',
];

// User-confirmed methods (via /api/identify search flow or
// /api/pin-track locked-album fast path). Treated as `confirmed-local`
// for pill purposes — the user vouched for the identity and Feature
// A is in the process of writing fingerprints behind it.
const CONFIRMED_USER_METHODS: Array<NowPlaying['match_method']> = [
  'user-identified',
  'user-selected',
];

interface MethodFlags {
  /** System-driven Shazam-class match (shazam, sonos-didl, sonos-polled). */
  shazam: boolean;
  /** System-driven local match (fingerprint). Excludes user-identified
      so a backend pin acknowledgment doesn't masquerade as Feature A
      having caught up early. */
  systemLocal: boolean;
  /** Any "this identity is confirmed" method (system OR user). */
  anyConfirmed: boolean;
}

function classifyMethod(method: NowPlaying['match_method']): MethodFlags {
  if (method === undefined) {
    return { shazam: false, systemLocal: false, anyConfirmed: false };
  }
  if (CONFIRMED_SHAZAM_METHODS.includes(method)) {
    return { shazam: true, systemLocal: false, anyConfirmed: true };
  }
  if (method === 'fingerprint') {
    return { shazam: false, systemLocal: true, anyConfirmed: true };
  }
  if (CONFIRMED_USER_METHODS.includes(method)) {
    return { shazam: false, systemLocal: false, anyConfirmed: true };
  }
  return { shazam: false, systemLocal: false, anyConfirmed: false };
}

function pinReleaseMatches(
  pinReleaseId: number | null, payload: NowPlaying,
): boolean {
  return pinReleaseId !== null && payload.release_id === pinReleaseId;
}

/**
 * Returns true while the identifying soft-window is still open.
 * Used in both the null-payload early-exit and `deriveWithoutPin` so
 * the two check-sites cannot drift independently.
 */
function inIdentifyingWindow(
  identifyingStartedAtMs: number | null,
  nowMs: number,
): boolean {
  return (
    identifyingStartedAtMs !== null &&
    nowMs - identifyingStartedAtMs < MOTION.identifyingTimeoutMs
  );
}

function deriveWithoutPin(
  payload: NowPlaying,
  flags: MethodFlags,
  identifyingStartedAtMs: number | null,
  nowMs: number,
): IdentifyState {
  if (flags.shazam) return 'confirmed-shazam';
  if (flags.systemLocal) return 'confirmed-local';
  if (flags.anyConfirmed) return 'confirmed-local'; // user-identified/user-selected
  if (payload.guess) return 'awaiting-confirm';
  // No recognition yet. If the identifying timer is still running, show
  // `identifying` instead of the loud `needs-id` failure state. After the
  // timeout elapses, fall through to `needs-id`.
  if (inIdentifyingWindow(identifyingStartedAtMs, nowMs)) {
    return 'identifying';
  }
  return 'needs-id';
}

function deriveWithinPinWindow(
  payload: NowPlaying, flags: MethodFlags, pinPosition: string | null,
): IdentifyState {
  // System caught up early at the same position → flip immediately.
  // ONLY system-driven methods qualify (user-identified is the
  // backend acknowledging our own pin and shouldn't count).
  if ((flags.shazam || flags.systemLocal) && payload.track_position === pinPosition) {
    return flags.shazam ? 'confirmed-shazam' : 'confirmed-local';
  }
  return 'user-pinned';
}

function deriveAfterPinGraduation(
  payload: NowPlaying, flags: MethodFlags, pinPosition: string | null,
): IdentifyState {
  // Pin's 50s window elapsed. Trust the pinned identity — Feature
  // A is expected to have written refs by now — but ONLY while the
  // kiosk's view of "what's playing" still matches the pin's
  // assertion. If the user lifted the needle and the payload now
  // shows a different track on the same album, fall through to
  // standard payload-based derivation. (impl-review-3 fix.)
  if (flags.shazam) return 'confirmed-shazam';
  if (payload.track_position === pinPosition) return 'confirmed-local';
  // Note: fall-through here does NOT pass identifyingStartedAtMs because
  // a post-pin-graduation fallback means we were confirmed for 50s — the
  // identifying timer is irrelevant (a new session would re-arm it).
  return deriveWithoutPin(payload, flags, null, 0);
}

/**
 * Pure derivation — no React state. Exported for unit testing.
 *
 * State transitions:
 *  - No pin, no payload, identifying timer active → identifying
 *  - No pin, no payload, timer elapsed (or null) → needs-id
 *  - No pin, with payload → deriveWithoutPin (standard mapping)
 *    - If no recognition AND identifying timer active → `identifying`
 *    - If no recognition AND timer elapsed (or null) → `needs-id`
 *  - Pin set, album changed → deriveWithoutPin (pin invalidated)
 *  - Pin set, within 50s → user-pinned UNLESS system caught up at
 *    the same position (shazam/fingerprint, NOT user-identified)
 *  - Pin set, past 50s → confirmed-local (or confirmed-shazam if
 *    Shazam landed). Never falls back to needs-id while the pin's
 *    album is still locked.
 */
export function deriveIdentifyState(
  payload: NowPlaying | null,
  pinAtMs: number | null,
  pinReleaseId: number | null,
  pinPosition: string | null,
  nowMs: number,
  identifyingStartedAtMs: number | null,
): IdentifyState {
  if (!payload) {
    // No payload yet (WS connecting, or transient reconnect gap).
    // If the identifying timer was already armed (e.g. we saw vinyl +
    // no-recognition before the gap), keep the soft UI rather than
    // flashing the loud `needs-id` failure screen for one render.
    return inIdentifyingWindow(identifyingStartedAtMs, nowMs) ? 'identifying' : 'needs-id';
  }
  const flags = classifyMethod(payload.match_method);

  // No pin set at all (or pin's album changed under us).
  if (pinAtMs === null || !pinReleaseMatches(pinReleaseId, payload)) {
    return deriveWithoutPin(payload, flags, identifyingStartedAtMs, nowMs);
  }

  if (nowMs - pinAtMs < MOTION.pinGraduationMs) {
    return deriveWithinPinWindow(payload, flags, pinPosition);
  }
  return deriveAfterPinGraduation(payload, flags, pinPosition);
}

export interface UseIdentifyState {
  identifyState: IdentifyState;
  /**
   * Track position the user most recently pinned, or null when no
   * pin is currently active (`identifyState !== 'user-pinned'`).
   * Surfaces from the hook's internal `pinPositionRef` so deep-tree
   * consumers (e.g. `TappableTrackRow`) can identify which row
   * owns the optimistic highlight without prop-drilling.
   */
  pinPosition: string | null;
  /**
   * Mark a local pin tap. Starts the 50s `user-pinned → confirmed-local`
   * graduation timer scoped to `(releaseId, position)`.
   */
  pin: (releaseId: number, position: string) => void;
  /**
   * Clear the pin overlay (e.g. on 4xx revert from /api/pin-track).
   * Restores derivation to pure payload-based state.
   */
  clearPin: () => void;
}

/**
 * Compute a stable album-session key for an unrecognized vinyl source.
 *
 * Returns `null` when not in an unrecognized vinyl session (non-vinyl source,
 * null payload, or recognition already arrived). The key only changes when
 * the *album* changes (release_id differs), not on every heartbeat — so
 * `prevSessionKeyRef` comparison only re-arms the identifying timer when a
 * genuinely new album session begins.
 *
 * `release_id` is the best available album discriminator:
 *  - Present from the first backend heartbeat once an album is locked
 *    (even before track recognition).
 *  - Stable across all heartbeats for the same album.
 *  - Changes when the user lifts the needle and drops a different record.
 *
 * When `release_id` is absent (album not yet locked — very early in the
 * first heartbeat for a completely new record), we use the special sentinel
 * `'no-release'`. If the album then locks (release_id becomes available)
 * while still unrecognized, the key changes and the timer re-arms — this is
 * correct because it signals a meaningfully new album context.
 */
function vinylUnrecognizedSessionKey(payload: NowPlaying | null): string | null {
  if (!payload || payload.source !== 'vinyl') return null;
  const flags = classifyMethod(payload.match_method);
  const hasRecognition = flags.anyConfirmed || !!payload.guess;
  if (hasRecognition) return null;
  return `${payload.release_id ?? 'no-release'}`;
}

export function useIdentifyState(payload: NowPlaying | null): UseIdentifyState {
  const [pinAtMs, setPinAtMs] = useState<number | null>(null);
  const pinReleaseIdRef = useRef<number | null>(null);
  const pinPositionRef = useRef<string | null>(null);
  // Tick state to drive re-renders when the 50s pin timer elapses.
  const [, setTick] = useState(0);

  // `identifying` timer — useState (not a ref) so the component re-renders
  // when the timer is first set (pill flips from nothing to `identifying`).
  // This mirrors the `pinAtMs` pattern: state changes trigger renders.
  const [identifyingStartedAtMs, setIdentifyingStartedAtMs] = useState<number | null>(null);

  // Track the last seen vinyl session key to detect new record drops.
  const prevSessionKeyRef = useRef<string | null>(null);

  // Schedule a one-shot tick when the 50s pin window is about to
  // expire so derivation recomputes (flips to confirmed-local via
  // deriveAfterPinGraduation) without waiting for the next WS
  // payload. pinAtMs is NOT cleared here — keeping it set lets
  // deriveAfterPinGraduation hold confirmed-local after the timer,
  // never reverting to needs-id while the album is still locked.
  // clearPin (explicit) and the album-change path
  // (pinReleaseMatches in deriveIdentifyState) are the only ways
  // out of the pin state.
  useEffect(() => {
    if (pinAtMs === null) return;
    const remaining = MOTION.pinGraduationMs - (Date.now() - pinAtMs);
    if (remaining <= 0) return; // Already past graduation; nothing to schedule.
    const t = setTimeout(() => setTick((n) => n + 1), remaining);
    return () => clearTimeout(t);
  }, [pinAtMs]);

  // Manage the `identifying` timer:
  //  1. When a new unrecognized vinyl album session begins (session key
  //     changes), arm the timer.
  //  2. When the timer elapses, fire a tick so derivation re-runs and
  //     falls through from `identifying` to `needs-id`.
  //  3. When recognition arrives (match or guess), `vinylUnrecognizedSessionKey`
  //     returns null → timer clears, no need to wait out the 45s.
  //  4. When source changes to non-vinyl, clear.
  //  5. When payload is null (WS gap / cold start before first heartbeat),
  //     DO NOT clear the timer — the timeout will expire naturally if
  //     the cascade never delivers a result. This lets `deriveIdentifyState`
  //     return `identifying` during the null-payload gap rather than
  //     flashing `needs-id` for one render.
  //
  // Key invariant: `vinylUnrecognizedSessionKey` is stable across heartbeats
  // for the same album (keyed on release_id, not ts), so re-running this
  // effect on every heartbeat is harmless — the session key won't change
  // until the album changes or recognition arrives.
  useEffect(() => {
    // Payload is null: WS not yet connected or briefly reconnecting.
    // Keep any existing timer running; don't clear prevSessionKeyRef.
    // `deriveIdentifyState` already handles the null-payload + active-timer
    // case via `inIdentifyingWindow`.
    if (payload === null) return;

    const sessionKey = vinylUnrecognizedSessionKey(payload);

    if (sessionKey === null) {
      // Not an unrecognized vinyl session (non-vinyl source or
      // recognition arrived) — clear the timer.
      prevSessionKeyRef.current = null;
      setIdentifyingStartedAtMs(null);
      return;
    }

    // New unrecognized vinyl album session (first time, or album changed).
    if (sessionKey !== prevSessionKeyRef.current) {
      prevSessionKeyRef.current = sessionKey;
      setIdentifyingStartedAtMs(Date.now());
    }
    // (Same session key → same album, still unrecognized. Don't touch the
    // timer; let it count down to `needs-id` naturally.)
  // Why: watching individual payload fields to avoid re-arming the timer on
  // every heartbeat. `payload` object identity changes every WS message, but
  // only the fields below affect session-key computation or recognition state.
  // The `null → object` transition is covered by `payload?.source` changing
  // from `undefined` to `'vinyl'`; adding `payload` itself would re-run the
  // effect on every heartbeat (impl-review-1 Should-fix).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload?.release_id, payload?.source, payload?.match_method, payload?.guess]);

  // Schedule a one-shot tick when the 45s identifying window is about to
  // expire so derivation re-runs (flips from `identifying` to `needs-id`)
  // without waiting for the next WS payload.
  useEffect(() => {
    if (identifyingStartedAtMs === null) return;
    const remaining = MOTION.identifyingTimeoutMs - (Date.now() - identifyingStartedAtMs);
    if (remaining <= 0) return; // Already past timeout; nothing to schedule.
    const t = setTimeout(() => setTick((n) => n + 1), remaining);
    return () => clearTimeout(t);
  }, [identifyingStartedAtMs]);

  const pin = useCallback((releaseId: number, position: string) => {
    pinReleaseIdRef.current = releaseId;
    pinPositionRef.current = position;
    setPinAtMs(Date.now());
  }, []);

  const clearPin = useCallback(() => {
    pinReleaseIdRef.current = null;
    pinPositionRef.current = null;
    setPinAtMs(null);
  }, []);

  const identifyState = deriveIdentifyState(
    payload,
    pinAtMs,
    pinReleaseIdRef.current,
    pinPositionRef.current,
    Date.now(),
    identifyingStartedAtMs,
  );

  // Expose pinPosition only while a pin is actually active — past
  // graduation or clearPin the ref still holds the value, but it's
  // semantically stale. Callers (TappableTrackRow) only care about
  // the position during the user-pinned visual, so we gate on it.
  const pinPosition =
    identifyState === 'user-pinned' ? pinPositionRef.current : null;

  return { identifyState, pinPosition, pin, clearPin };
}
