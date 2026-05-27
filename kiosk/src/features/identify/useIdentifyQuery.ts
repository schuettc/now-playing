import { useCallback, useEffect, useRef } from 'react';
import { applyTokenMatch } from './searchMatch';
import type { SearchResponse } from './types';

const DEBOUNCE_MS = 200;

type Setters = {
  setSearchQuery: (v: string) => void;
  setSearchResults: (v: SearchResponse | null) => void;
  setExpandedReleaseId: (v: number | null) => void;
  setHighlightedTrackPosition: (v: string | null) => void;
  setAlbumPickTrackTitle: (v: string | null) => void;
  showToast: (msg: string, isError?: boolean) => void;
};

async function performSearch(
  trimmed: string,
  skipAutopilot: boolean,
  isStale: () => boolean,
  c: Setters,
): Promise<void> {
  try {
    const r = await fetch(
      `/api/collection/search?q=${encodeURIComponent(trimmed)}`,
    );
    const data: SearchResponse = await r.json();
    if (isStale()) return;
    c.setSearchResults(data);
    const { releaseId, position } = skipAutopilot
      ? { releaseId: null, position: null }
      : applyTokenMatch(data, trimmed);
    c.setExpandedReleaseId(releaseId);
    c.setHighlightedTrackPosition(position);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    c.setSearchResults({ items: [], groups: [] });
    c.showToast(`Search failed: ${msg}`, true);
  }
}

export function useIdentifyQuery(s: Setters) {
  const debounceTimer = useRef<number | null>(null);
  const lastSearchRef = useRef<string>('');
  const settersRef = useRef(s);
  settersRef.current = s;

  useEffect(
    () => () => {
      if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    },
    [],
  );

  const resetResults = useCallback(() => {
    const c = settersRef.current;
    c.setSearchResults(null);
    c.setExpandedReleaseId(null);
    c.setHighlightedTrackPosition(null);
  }, []);

  const runSearch = useCallback(
    async (q: string, opts?: { skipAutopilot?: boolean }) => {
      const trimmed = q.trim();
      lastSearchRef.current = trimmed;
      if (trimmed.length < 2) return resetResults();
      await performSearch(
        trimmed,
        !!opts?.skipAutopilot,
        () => lastSearchRef.current !== trimmed,
        settersRef.current,
      );
    },
    [resetResults],
  );

  const onSearchInput = useCallback(
    (value: string) => {
      const c = settersRef.current;
      c.setSearchQuery(value);
      c.setAlbumPickTrackTitle(null);
      if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
      if (value.trim().length < 2) return resetResults();
      debounceTimer.current = window.setTimeout(
        () => runSearch(value),
        DEBOUNCE_MS,
      );
    },
    [resetResults, runSearch],
  );

  return { runSearch, onSearchInput };
}
