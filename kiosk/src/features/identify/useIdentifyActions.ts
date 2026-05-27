import { useCallback } from 'react';
import { useSubmit, type SubmitArgs } from './useSubmit';
import { resolveToggleExpanded } from './identifyActionHelpers';
import type { SearchRelease, SearchResponse, SearchTrack } from './types';

export { findTrackInRelease } from './identifyActionHelpers';

interface Args extends SubmitArgs {
  searchResults: SearchResponse | null;
  albumPickTrackTitle: string | null;
  expandedReleaseId: number | null;
}

/**
 * Identify-route action handlers: wraps `useSubmit` for the
 * (release_id, track_position) pick, handles album-pick mode's
 * tap-to-resolve shortcut, and manages the expanded-album state.
 *
 * Returns `submittingTrackKey` (from `useSubmit`) so the renderer can
 * disable the rest of the grid while a pick is in flight.
 */
export function useIdentifyActions({
  searchResults,
  albumPickTrackTitle,
  expandedReleaseId,
  setSearchQuery,
  setSearchResults,
  setExpandedReleaseId,
  setHighlightedTrackPosition,
  showToast,
  onPickSuccess,
}: Args) {
  const { submittingTrackKey, submitIdentify } = useSubmit({
    setSearchQuery,
    setSearchResults,
    setExpandedReleaseId,
    setHighlightedTrackPosition,
    showToast,
    onPickSuccess,
  });

  const onTrackPick = useCallback(
    (rel: SearchRelease, track: SearchTrack) => {
      if (submittingTrackKey !== null) return;
      submitIdentify(rel.release_id, track.position ?? '');
    },
    [submitIdentify, submittingTrackKey],
  );

  const toggleExpanded = useCallback(
    (releaseId: number) => {
      const decision = resolveToggleExpanded({
        releaseId,
        searchResults,
        albumPickTrackTitle,
        expandedReleaseId,
        isSubmitting: submittingTrackKey !== null,
      });
      if (decision.kind === 'submit') {
        submitIdentify(decision.releaseId, decision.position);
        return;
      }
      if (decision.kind === 'collapse') {
        setExpandedReleaseId(null);
        return;
      }
      setHighlightedTrackPosition(null);
      setExpandedReleaseId(decision.releaseId);
    },
    [
      albumPickTrackTitle,
      expandedReleaseId,
      searchResults,
      submitIdentify,
      submittingTrackKey,
      setExpandedReleaseId,
      setHighlightedTrackPosition,
    ],
  );

  return { submittingTrackKey, onTrackPick, toggleExpanded };
}
