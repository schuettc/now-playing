/**
 * Context provider so deep-tree confirm-first surfaces (GuessConfirm,
 * TappableTrackRow, LookupView's scoped tracklist) can reach
 * `useIdentifyState`'s `pin()` / `clearPin()` actions without
 * threading them through every prop chain.
 *
 * Provider lives in `App.tsx` wrapping the entire `<Switch>` (lifted
 * in D-4 so the pin lifecycle survives route transitions between
 * `/`, `/lookup`, and `/identify`). Consumers throw if used outside.
 */
import { createContext, useContext } from 'react';
import type { UseIdentifyState } from './useIdentifyState';

const IdentifyContext = createContext<UseIdentifyState | null>(null);

export const IdentifyProvider = IdentifyContext.Provider;

export function useIdentifyContext(): UseIdentifyState {
  const ctx = useContext(IdentifyContext);
  if (!ctx) {
    throw new Error(
      'useIdentifyContext must be used inside an IdentifyProvider',
    );
  }
  return ctx;
}
