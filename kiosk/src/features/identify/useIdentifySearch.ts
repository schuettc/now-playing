import { useCallback, useRef, useState } from 'react';
import { useStore } from '@/store/useStore';
import { useIdentifyQuery } from './useIdentifyQuery';
import { useToast } from './useToast';

export {
  applyTokenMatch,
  findTrackMatch,
  hasArtistOrAlbumMatch,
} from './searchMatch';

/**
 * Owns all search-related state for the /identify route: the query +
 * results (proxied to the Zustand store so cross-component navigation
 * preserves them), the in-page expansion state, the album-pick mode
 * flag, debounce timing, and toast notifications (via `useToast`).
 */
export function useIdentifySearch() {
  const searchQuery = useStore((s) => s.searchQuery);
  const setSearchQuery = useStore((s) => s.setSearchQuery);
  const searchResults = useStore((s) => s.searchResults);
  const setSearchResults = useStore((s) => s.setSearchResults);

  const [expandedReleaseId, setExpandedReleaseId] = useState<number | null>(
    null,
  );
  const [highlightedTrackPosition, setHighlightedTrackPosition] =
    useState<string | null>(null);
  // Set when /identify is opened in scope=album mode. While non-null,
  // tapping any album card auto-resolves the track on THAT release by
  // matching this title and submits — the user shouldn't have to pick
  // the song a second time, since 'Wrong album' implies the song name
  // was correct.
  const [albumPickTrackTitle, setAlbumPickTrackTitle] = useState<
    string | null
  >(null);
  const { toast, showToast } = useToast();
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const { runSearch, onSearchInput } = useIdentifyQuery({
    setSearchQuery,
    setSearchResults,
    setExpandedReleaseId,
    setHighlightedTrackPosition,
    setAlbumPickTrackTitle,
    showToast,
  });

  const clearSearch = useCallback(() => {
    setSearchQuery('');
    setSearchResults(null);
    setExpandedReleaseId(null);
    setHighlightedTrackPosition(null);
  }, [setSearchQuery, setSearchResults]);

  return {
    searchQuery,
    searchResults,
    setSearchQuery,
    setSearchResults,
    expandedReleaseId,
    setExpandedReleaseId,
    highlightedTrackPosition,
    setHighlightedTrackPosition,
    albumPickTrackTitle,
    setAlbumPickTrackTitle,
    toast,
    showToast,
    searchInputRef,
    runSearch,
    onSearchInput,
    clearSearch,
  };
}
