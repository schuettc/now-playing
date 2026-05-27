import { useCallback, useState } from 'react';
import { useLocation } from 'wouter';
import type { SearchResponse } from './types';

const POST_SUBMIT_REDIRECT_MS = 1500;

export interface SubmitArgs {
  setSearchQuery: (q: string) => void;
  setSearchResults: (r: SearchResponse | null) => void;
  setExpandedReleaseId: (n: number | null) => void;
  setHighlightedTrackPosition: (p: string | null) => void;
  showToast: (msg: string, error?: boolean) => void;
  /** Called on POST success — callers use this to set pickedRef once the
   *  submission is confirmed, so a failed POST never masks abandonment. */
  onPickSuccess?: () => void;
}

/** Args for the pure POST logic, extracted for testability. */
export interface PostIdentifyArgs {
  releaseId: number;
  trackPosition: string;
  showToast: (msg: string, error?: boolean) => void;
  onPickSuccess?: () => void;
}

/**
 * Pure async POST to `/api/identify`. Extracted from `useSubmit` so the
 * success/failure branching — including the `onPickSuccess` callback
 * that guards `pickedRef` — can be unit-tested without a React renderer.
 *
 * Returns `{ ok: true }` on success and `{ ok: false, error }` on failure
 * (non-ok HTTP status or thrown network error).
 */
export async function postIdentify({
  releaseId,
  trackPosition,
  showToast,
  onPickSuccess,
}: PostIdentifyArgs): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const r = await fetch('/api/identify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        release_id: releaseId,
        track_position: trackPosition,
      }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    onPickSuccess?.();
    showToast(`Set as playing · ${trackPosition}`);
    return { ok: true };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    showToast(`Failed: ${msg}`, true);
    return { ok: false, error: msg };
  }
}

/**
 * Submit handler for the /identify route. Owns `submittingTrackKey`
 * (so the renderer can disable the grid while a pick is in flight) and
 * the `submitIdentify(releaseId, trackPosition)` POST that clears
 * search state and redirects to the kiosk home on success.
 *
 * Returns `{ submittingTrackKey, submitIdentify }`. The action callers
 * (track pick, album-pick mode shortcut) live in `useIdentifyActions`.
 */
export function useSubmit({
  setSearchQuery,
  setSearchResults,
  setExpandedReleaseId,
  setHighlightedTrackPosition,
  showToast,
  onPickSuccess,
}: SubmitArgs) {
  const [, setLocation] = useLocation();
  const [submittingTrackKey, setSubmittingTrackKey] = useState<string | null>(
    null,
  );

  const submitIdentify = useCallback(
    async (releaseId: number, trackPosition: string) => {
      const key = `${releaseId}-${trackPosition}`;
      setSubmittingTrackKey(key);
      const result = await postIdentify({
        releaseId,
        trackPosition,
        showToast,
        onPickSuccess,
      });
      if (result.ok) {
        setSearchQuery('');
        setSearchResults(null);
        setExpandedReleaseId(null);
        setHighlightedTrackPosition(null);
        window.setTimeout(() => {
          setLocation('/');
        }, POST_SUBMIT_REDIRECT_MS);
      } else {
        setSubmittingTrackKey(null);
      }
    },
    [
      setLocation,
      setSearchQuery,
      setSearchResults,
      setExpandedReleaseId,
      setHighlightedTrackPosition,
      showToast,
      onPickSuccess,
    ],
  );

  return { submittingTrackKey, submitIdentify };
}
