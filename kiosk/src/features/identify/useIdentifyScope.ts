import { useEffect, type RefObject } from 'react';
import {
  computeSeed,
  fetchNowPlayingPayload,
  parseScope,
  type IdentifyScope,
} from './identifyScopeHelpers';

interface Args {
  setSearchQuery: (q: string) => void;
  runSearch: (q: string, opts?: { skipAutopilot?: boolean }) => void;
  setAlbumPickTrackTitle: (s: string | null) => void;
  searchInputRef: RefObject<HTMLInputElement | null>;
}

/**
 * Reads `?scope=track|album` from the URL once on mount and prefills
 * the search query from the orchestrator's current now-playing
 * payload:
 *
 *   - `scope=track` — seed with the (possibly wrong) track title so
 *     the user can edit toward the right one.
 *   - `scope=album` — track is right, album is wrong. Seed with
 *     "<artist> <title>" so EVERY release with this track shows up.
 *     Skip the track-title autopilot (we don't want anything to
 *     auto-expand); enable album-pick mode so tapping any result auto-
 *     resolves the track on that release without asking the user to
 *     pick the song again.
 *
 * No scope → focus the search input and return. The cancellation flag
 * guards against HMR / fast remounts double-firing the now-playing
 * fetch.
 */
export function useIdentifyScope({
  setSearchQuery,
  runSearch,
  setAlbumPickTrackTitle,
  searchInputRef,
}: Args): void {
  useEffect(() => {
    let cancelled = false;
    const scope = parseScope(window.location.search);
    if (!scope) {
      searchInputRef.current?.focus();
      return;
    }
    void applyScope(scope, () => cancelled, {
      setSearchQuery,
      runSearch,
      setAlbumPickTrackTitle,
      searchInputRef,
    });
    return () => {
      cancelled = true;
    };
    // Intentional mount-only effect. The scope is read once from the
    // URL on entry to /identify; all dependencies (setSearchQuery,
    // runSearch, setAlbumPickTrackTitle, searchInputRef) are stable
    // across renders (useCallback-backed in the search hook), so
    // including them here would be a no-op semantically. We list them
    // anyway to keep the dep array honest and let the lint pass
    // without a suppression.
  }, [setSearchQuery, runSearch, setAlbumPickTrackTitle, searchInputRef]);
}

/**
 * Apply the computed seed result to the search state. Focuses the input
 * when there is no actionable seed (unknown payload or empty seed string).
 */
function handleScopeResult(
  seed: string,
  skipAutopilot: boolean,
  albumPickTrackTitle: string | null,
  { setSearchQuery, runSearch, setAlbumPickTrackTitle, searchInputRef }: Args,
): void {
  if (albumPickTrackTitle) setAlbumPickTrackTitle(albumPickTrackTitle);
  if (!seed) {
    searchInputRef.current?.focus();
    return;
  }
  setSearchQuery(seed);
  runSearch(seed, { skipAutopilot });
}

async function applyScope(
  scope: IdentifyScope,
  isCancelled: () => boolean,
  args: Args,
): Promise<void> {
  const payload = await fetchNowPlayingPayload();
  if (isCancelled()) return;
  if (!payload) {
    args.searchInputRef.current?.focus();
    return;
  }
  const { seed, skipAutopilot, albumPickTrackTitle } = computeSeed(scope, payload);
  handleScopeResult(seed, skipAutopilot, albumPickTrackTitle, args);
}
