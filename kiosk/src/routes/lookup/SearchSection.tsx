import { useCallback, useRef, useState } from 'react';
import { ResultsGrid } from '@/features/identify/ResultsGrid';
import { Toast } from '@/features/identify/Toast';
import { resolveToggleExpanded } from '@/features/identify/identifyActionHelpers';
import { useGridCells } from '@/features/identify/useGridCells';
import { useIdentifyActions } from '@/features/identify/useIdentifyActions';
import { useIdentifyScope } from '@/features/identify/useIdentifyScope';
import { useIdentifySearch } from '@/features/identify/useIdentifySearch';
import { SearchField } from '@/components/touch/SearchField';
import { VirtualKeyboard } from '@/components/touch/VirtualKeyboard';
import { track as telemetryTrack } from '@/lib/telemetry';
import type { LookupVariant } from '@/lib/lookupVariant';
import { usePickedRef } from './pickedContext';

export type SearchHandle = ReturnType<typeof useIdentifySearch>;

/**
 * Hook to instantiate the lookup-route search state ONCE per
 * variant mount. Variants call this and pass the returned handle
 * to <SearchSection /> AND wrap children in
 * <SearchSeedProvider value={handle.onSearchInput}> so sibling
 * surfaces (RecentsHero, ArtistChips) can seed + trigger a search.
 */
export function useLookupSearch(): SearchHandle {
  return useIdentifySearch();
}

interface Props {
  variant: LookupVariant;
  /** Larger search field for the search-first hero treatment. */
  searchSize?: 'md' | 'lg';
  mountedAtMs: number;
  /** Lifted from the variant so siblings share one instance. */
  search: SearchHandle;
}

/**
 * Shared search section used by all three LookupView variants. Wraps
 * the legacy `features/identify/` machinery (search hooks + grid)
 * behind a touch-first SearchField. The differences between variants
 * are above this section (hero recents row vs. nothing), not within.
 *
 * Telemetry: fires `identify_lookup_pick` on a successful track
 * submission via the legacy `useIdentifyActions.onTrackPick`. The
 * `ms_to_pick` dimension measures from the variant component's mount
 * (`mountedAtMs` prop) to the tap.
 */
export function SearchSection({
  variant, searchSize = 'md', mountedAtMs, search,
}: Props) {
  const pickedRef = usePickedRef();
  useIdentifyScope({
    setSearchQuery: search.setSearchQuery,
    runSearch: search.runSearch,
    setAlbumPickTrackTitle: search.setAlbumPickTrackTitle,
    searchInputRef: search.searchInputRef,
  });

  // Move pickedRef set to POST success so a failed network call never
  // masks the identify_lookup_dismiss abandonment event.
  const onPickSuccess = useCallback(
    () => { pickedRef.current = true; },
    [pickedRef],
  );

  const baseActions = useIdentifyActions({
    searchResults: search.searchResults,
    albumPickTrackTitle: search.albumPickTrackTitle,
    expandedReleaseId: search.expandedReleaseId,
    setSearchQuery: search.setSearchQuery,
    setSearchResults: search.setSearchResults,
    setExpandedReleaseId: search.setExpandedReleaseId,
    setHighlightedTrackPosition: search.setHighlightedTrackPosition,
    showToast: search.showToast,
    onPickSuccess,
  });

  const onTrackPick = useCallback(
    (...args: Parameters<typeof baseActions.onTrackPick>) => {
      // Guard mirrors baseActions.onTrackPick so telemetry never
      // over-reports on a double-tap or race against an in-flight POST.
      if (baseActions.submittingTrackKey !== null) return;
      telemetryTrack('identify_lookup_pick', {
        variant,
        // A manual track tap inside an expanded album is always a
        // track-pick, even in album-pick mode (the user overrode the
        // auto-resolve).
        picked_album: false,
        picked_track: true,
        ms_to_pick: Date.now() - mountedAtMs,
      });
      baseActions.onTrackPick(...args);
    },
    [baseActions, variant, mountedAtMs],
  );

  const toggleExpanded = useCallback(
    (releaseId: number) => {
      // Peek at the decision: when album-pick mode resolves to an
      // immediate submit, fire the album-pick telemetry event before
      // delegating to the base action. For expand/collapse we just
      // delegate with no telemetry — those aren't picks.
      const decision = resolveToggleExpanded({
        releaseId,
        searchResults: search.searchResults,
        albumPickTrackTitle: search.albumPickTrackTitle,
        expandedReleaseId: search.expandedReleaseId,
        isSubmitting: baseActions.submittingTrackKey !== null,
      });
      if (decision.kind === 'submit') {
        telemetryTrack('identify_lookup_pick', {
          variant,
          picked_album: true,
          picked_track: false,
          ms_to_pick: Date.now() - mountedAtMs,
        });
      }
      baseActions.toggleExpanded(releaseId);
    },
    [baseActions, variant, mountedAtMs, search.searchResults, search.albumPickTrackTitle, search.expandedReleaseId],
  );

  const gridCells = useGridCells({
    searchResults: search.searchResults,
    expandedReleaseId: search.expandedReleaseId,
  });

  // Virtual-keyboard collapse: when the on-screen keyboard is up, hide
  // the grid until results are typed. Aligns with the design's
  // "virtual keyboard takes ~50% vertical space" guidance. Driven by an
  // explicit state because the keys preventDefault to retain input focus,
  // so onBlur alone can't tell us the keyboard is dismissed.
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const showGrid = !keyboardVisible || search.searchQuery.length > 0;

  return (
    <section
      className="flex flex-col gap-4"
      style={keyboardVisible ? { paddingBottom: '50vh' } : undefined}
    >
      <SearchField
        ref={search.searchInputRef}
        value={search.searchQuery}
        onChange={search.onSearchInput}
        onFocus={() => setKeyboardVisible(true)}
        size={searchSize}
        placeholder="Search artist, album, or catalog number…"
      />
      {showGrid && (
        <ResultsGrid
          cells={gridCells}
          expandedReleaseId={search.expandedReleaseId}
          highlightedTrackPosition={search.highlightedTrackPosition}
          submittingTrackKey={baseActions.submittingTrackKey}
          onToggleExpanded={toggleExpanded}
          onTrackPick={onTrackPick}
        />
      )}
      <Toast toast={search.toast} />
      <VirtualKeyboard
        visible={keyboardVisible}
        value={search.searchQuery}
        onChange={search.onSearchInput}
        onClear={search.clearSearch}
        onDone={() => {
          setKeyboardVisible(false);
          search.searchInputRef.current?.blur();
        }}
      />
    </section>
  );
}

/**
 * Hook to capture the variant component's mount timestamp once
 * for telemetry's `ms_to_pick` / `ms_in_view` dimensions.
 */
export function useMountedAt(): number {
  const ref = useRef(Date.now());
  return ref.current;
}
