import { useMemo } from 'react';
import { buildGridCells } from './gridCellsHelpers';
import type { GridCell, SearchResponse } from './types';

interface Args {
  searchResults: SearchResponse | null;
  expandedReleaseId: number | null;
}

/**
 * Derive the ordered cell list for the results grid: group headers
 * interleaved with release cards. When an album is expanded, hoist it
 * to the first slot within its group (or within the leftovers
 * section) so the user-tapped card doesn't sit below earlier-sorted
 * siblings (e.g. Failure: Comfort before Fantastic Planet).
 *
 * Returns `null` when the search is empty (renderer shows nothing) and
 * `[]` when the search returned but matched nothing (renderer shows
 * the "No matches" placeholder).
 */
export function useGridCells({
  searchResults,
  expandedReleaseId,
}: Args): GridCell[] | null {
  return useMemo(
    () => buildGridCells(searchResults, expandedReleaseId),
    [searchResults, expandedReleaseId],
  );
}
