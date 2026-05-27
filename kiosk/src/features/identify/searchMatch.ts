import type { SearchRelease, SearchResponse } from './types';

function flattenReleases(data: SearchResponse): SearchRelease[] {
  const buckets: SearchRelease[] = [];
  for (const g of data.groups || [])
    for (const rel of g.releases || []) buckets.push(rel);
  for (const rel of data.items || []) buckets.push(rel);
  return buckets;
}

/**
 * True iff `needle` matches any group artist, release title, or
 * release artist in `data`. When true, the token-match autopilot
 * should NOT auto-expand a release — typing "failure" should leave
 * the Failure-the-artist results visible rather than jumping into an
 * album whose tracklist happens to contain a "Failure" track.
 *
 * `needle` must already be trimmed + lowercased.
 */
export function hasArtistOrAlbumMatch(
  data: SearchResponse,
  needle: string,
): boolean {
  if (!needle) return false;
  if (
    (data.groups || []).some((g) =>
      (g.artist || '').toLowerCase().includes(needle),
    )
  ) {
    return true;
  }
  return flattenReleases(data).some(
    (rel) =>
      (rel.title || '').toLowerCase().includes(needle) ||
      (rel.artist || '').toLowerCase().includes(needle),
  );
}

/**
 * Walk every release's tracklist and return the first
 * `{ releaseId, position }` whose track title contains `needle`, or
 * `null` if nothing matches. `needle` must already be trimmed +
 * lowercased.
 */
export function findTrackMatch(
  data: SearchResponse,
  needle: string,
): { releaseId: number; position: string | null } | null {
  if (!needle) return null;
  for (const rel of flattenReleases(data)) {
    for (const t of rel.tracks || []) {
      if ((t.title || '').toLowerCase().includes(needle)) {
        return { releaseId: rel.release_id, position: t.position ?? null };
      }
    }
  }
  return null;
}

/**
 * Token-match autopilot: pick the release whose track title matches
 * the search token and stash its position so the renderer can
 * highlight it.
 *
 * Only fires when the token DOESN'T match at the artist or album
 * level. Typing "failure" should leave the Failure-the-artist results
 * visible; it shouldn't auto-jump into ATUM just because one of its
 * 33 tracks is titled "Failure".
 */
export function applyTokenMatch(
  data: SearchResponse,
  token: string,
): { releaseId: number | null; position: string | null } {
  const needle = token.trim().toLowerCase();
  if (!needle) return { releaseId: null, position: null };
  if (hasArtistOrAlbumMatch(data, needle)) {
    return { releaseId: null, position: null };
  }
  const hit = findTrackMatch(data, needle);
  return hit ?? { releaseId: null, position: null };
}
