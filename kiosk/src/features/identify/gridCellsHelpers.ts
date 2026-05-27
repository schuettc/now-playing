import type {
  GridCell,
  SearchRelease,
  SearchResponse,
} from './types';

export function hoistExpanded(
  releases: SearchRelease[],
  expandedReleaseId: number | null,
): SearchRelease[] {
  if (expandedReleaseId === null) return releases;
  const idx = releases.findIndex((r) => r.release_id === expandedReleaseId);
  if (idx <= 0) return releases;
  const next = [...releases];
  const [picked] = next.splice(idx, 1);
  next.unshift(picked);
  return next;
}

function pushGroupCells(
  cells: GridCell[],
  groupedIds: Set<number>,
  group: { artist: string; releases?: SearchRelease[] },
  expandedReleaseId: number | null,
): void {
  const releases = group.releases || [];
  cells.push({ kind: 'header', label: group.artist, count: releases.length });
  for (const rel of hoistExpanded(releases, expandedReleaseId)) {
    groupedIds.add(rel.release_id);
    cells.push({ kind: 'release', rel });
  }
}

function pushLeftoverCells(
  cells: GridCell[],
  leftovers: SearchRelease[],
  hasGroups: boolean,
  expandedReleaseId: number | null,
): void {
  if (leftovers.length === 0) return;
  if (hasGroups) {
    cells.push({
      kind: 'header',
      label: 'Other matches',
      count: leftovers.length,
    });
  }
  for (const rel of hoistExpanded(leftovers, expandedReleaseId)) {
    cells.push({ kind: 'release', rel });
  }
}

/**
 * Pure derivation of the ordered cell list. Returns `null` for "no
 * search yet" and `[]` for "search returned nothing"; the renderer
 * distinguishes these to choose between empty state and "no matches".
 */
export function buildGridCells(
  searchResults: SearchResponse | null,
  expandedReleaseId: number | null,
): GridCell[] | null {
  if (!searchResults) return null;
  const items = searchResults.items || [];
  const groups = searchResults.groups || [];
  if (!items.length && !groups.length) return [];

  const cells: GridCell[] = [];
  const groupedIds = new Set<number>();
  for (const g of groups) {
    pushGroupCells(cells, groupedIds, g, expandedReleaseId);
  }
  const leftovers = items.filter((rel) => !groupedIds.has(rel.release_id));
  pushLeftoverCells(cells, leftovers, groups.length > 0, expandedReleaseId);
  return cells;
}
