import { useCallback, useRef } from 'react';
import { useIdentifyContext } from '@/hooks/identifyContext';
import { useStore } from '@/store/useStore';
import { MOTION } from '@/lib/motion';
import { track as telemetryTrack } from '@/lib/telemetry';

export type PinEntry = 'guess' | 'tracklist' | 'lookup' | 'alt';

export interface PinTrackArgs {
  release_id: number;
  track_position: string;
  entry: PinEntry;
  /**
   * True when the target release_id matches the currently-locked album.
   * When true, calls `/api/pin-track` (fast path — requires lock match).
   * When false, calls `/api/identify` (accepts any catalog release).
   * Defaults to `true` so existing callers that don't pass this flag
   * retain the original behaviour.
   */
  isCurrentAlbum?: boolean;
}

export interface PinTrackResult {
  ok: boolean;
  reason?: string;
}

async function parseReason(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { reason?: string };
    return body.reason ?? 'unknown';
  } catch {
    return 'unknown';
  }
}

function errReason(err: unknown): string {
  const isAbort = err instanceof DOMException && err.name === 'AbortError';
  return isAbort ? 'timeout' : 'network';
}

/**
 * POST a track action to the given endpoint. Shared by `/api/pin-track`
 * (current-album fast path) and `/api/identify` (past-album recents path).
 *
 * `/api/pin-track` requires `release_id` to match the currently-locked
 * album and returns 400 `release-id-mismatch` otherwise. `/api/identify`
 * accepts any catalog release and publishes the full payload as
 * "user-identified" — the same endpoint used by the search-and-pick flow.
 */
async function postTrackAction(
  endpoint: '/api/pin-track' | '/api/identify',
  release_id: number,
  track_position: string,
): Promise<PinTrackResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), MOTION.pinTrackTimeoutMs);
  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id, track_position }),
      signal: controller.signal,
    });
    return resp.ok
      ? { ok: true }
      : { ok: false, reason: await parseReason(resp) };
  } catch (err) {
    return { ok: false, reason: errReason(err) };
  } finally {
    clearTimeout(timer);
  }
}

function reportFailure(reason: string, entry: PinEntry): void {
  if (reason === 'timeout') {
    telemetryTrack('identify_pin_timeout', { entry });
  } else {
    telemetryTrack('identify_pin_4xx', { reason, entry });
  }
}

async function handlePinResult(
  result: PinTrackResult,
  entry: PinEntry,
  myRequestId: number,
  latestRequestId: { current: number },
  identify: ReturnType<typeof useIdentifyContext>,
): Promise<PinTrackResult> {
  if (result.ok) return result;
  const reason = result.reason ?? 'unknown';
  reportFailure(reason, entry);
  // Only revert if we're still the latest request — a newer tap
  // may have already moved the pin forward.
  if (latestRequestId.current === myRequestId) {
    identify.clearPin();
    useStore.getState().setPinErrorReason(reason);
  }
  return result;
}

/**
 * Hook returning a `pinTrack` callback that performs the optimistic-
 * UI sequence:
 *  1. Locally flip `IdentifyState` to `user-pinned` via context's `pin()`.
 *  2. Fire `pulseLearningChip()` — the chip is the ack.
 *  3. POST the appropriate endpoint based on `isCurrentAlbum`:
 *     - `true` (default): POST `/api/pin-track` — fast path for the
 *       currently-locked album; requires release_id to match the lock.
 *     - `false`: POST `/api/identify` — accepts any catalog release;
 *       used for past-album recents taps (D-5 one-tap recents feature).
 *  4. On 4xx / timeout / network error: call `clearPin()` to revert
 *     the optimistic flip, set `pinErrorReason` so `InlineError`
 *     surfaces the failure, fire telemetry.
 *  5. On 200: success — the WS reconciliation will land the canonical
 *     payload soon.
 *
 * In-flight tracking: a ref-based request id ensures a stale 4xx
 * from an earlier tap can't revert a newer in-flight tap.
 *
 * See `docs/features/identify-guess-confirm/plan.md` Step 8.
 * See `docs/features/identify-lookup-recents-one-tap/plan.md` Step 6.
 */
export function usePinTrack(): (args: PinTrackArgs) => Promise<PinTrackResult> {
  const identify = useIdentifyContext();
  const latestRequestId = useRef(0);

  return useCallback(
    async (args: PinTrackArgs): Promise<PinTrackResult> => {
      const { release_id, track_position, entry, isCurrentAlbum = true } = args;
      const myRequestId = ++latestRequestId.current;
      identify.pin(release_id, track_position);
      useStore.getState().pulseLearningChip();
      const endpoint = isCurrentAlbum ? '/api/pin-track' : '/api/identify';
      const result = await postTrackAction(endpoint, release_id, track_position);
      return handlePinResult(result, entry, myRequestId, latestRequestId, identify);
    },
    [identify],
  );
}
