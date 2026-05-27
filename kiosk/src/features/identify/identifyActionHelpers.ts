import type { SearchRelease, SearchResponse, SearchTrack } from './types';

/**
 * Walks `searchResults` for the release matching `releaseId` and
 * returns the first track whose title equals (or, failing that,
 * contains) `trackTitle`. Used to short-circuit the "pick a track"
 * step in album-pick mode — when the user already told us the song
 * name was right, just find that song on the new album they tapped.
 */
export function findTrackInRelease(
  searchResults: SearchResponse | null,
  releaseId: number,
  trackTitle: string,
): SearchTrack | null {
  if (!searchResults) return null;
  const needle = trackTitle.trim().toLowerCase();
  if (!needle) return null;
  const allReleases: SearchRelease[] = [];
  for (const g of searchResults.groups || [])
    for (const r of g.releases || []) allReleases.push(r);
  for (const r of searchResults.items || []) allReleases.push(r);
  const rel = allReleases.find((r) => r.release_id === releaseId);
  if (!rel) return null;
  const ts = rel.tracks || [];
  return (
    ts.find((t) => (t.title || '').toLowerCase() === needle) ??
    ts.find((t) => (t.title || '').toLowerCase().includes(needle)) ??
    null
  );
}

export type ToggleDecision =
  | { kind: 'submit'; releaseId: number; position: string }
  | { kind: 'collapse' }
  | { kind: 'expand'; releaseId: number };

interface ToggleInput {
  releaseId: number;
  searchResults: SearchResponse | null;
  albumPickTrackTitle: string | null;
  expandedReleaseId: number | null;
  isSubmitting: boolean;
}

/**
 * Pure decision for a tap on an album card. Album-pick mode tries to
 * resolve the pre-set track on the tapped release and submit directly;
 * if no match, falls through to the normal expand/collapse flow so the
 * user can still resolve it manually.
 */
export function resolveToggleExpanded({
  releaseId,
  searchResults,
  albumPickTrackTitle,
  expandedReleaseId,
  isSubmitting,
}: ToggleInput): ToggleDecision {
  if (albumPickTrackTitle && !isSubmitting) {
    const t = findTrackInRelease(searchResults, releaseId, albumPickTrackTitle);
    if (t?.position) return { kind: 'submit', releaseId, position: t.position };
  }
  if (expandedReleaseId === releaseId) return { kind: 'collapse' };
  return { kind: 'expand', releaseId };
}
