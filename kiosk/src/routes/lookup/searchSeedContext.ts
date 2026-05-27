/**
 * Lets sibling surfaces (RecentsHero, ArtistChips) seed the
 * SearchSection's query AND trigger an actual search — not just
 * update the input text via the Zustand store.
 *
 * `useIdentifyQuery`'s debounce timer is per-instance, so calling
 * `setSearchQuery` alone won't fire a search. Surfaces above the
 * SearchSection consume `useSearchSeed()` and call the exposed
 * `seed(query)` callback, which routes through SearchSection's
 * `onSearchInput` (sets query + schedules debounced search).
 */
import { createContext, useContext } from 'react';

export type SeedSearch = (query: string) => void;

const SearchSeedContext = createContext<SeedSearch | null>(null);

export const SearchSeedProvider = SearchSeedContext.Provider;

export function useSearchSeed(): SeedSearch {
  const seed = useContext(SearchSeedContext);
  if (!seed) {
    throw new Error('useSearchSeed must be used inside a SearchSeedProvider');
  }
  return seed;
}
