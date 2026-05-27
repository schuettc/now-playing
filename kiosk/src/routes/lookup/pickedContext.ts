/**
 * Tiny context for the LookupView orchestrator's dismiss-telemetry
 * gate. Variants call `usePickedRef().current = true` from their
 * tap handlers BEFORE navigating away; the orchestrator's cleanup
 * effect reads the ref and suppresses `identify_lookup_dismiss`
 * when a pick actually happened.
 *
 * Without this gate, every successful pick fires both `pick` AND
 * `dismiss` events on unmount, skewing abandonment metrics to
 * ~0% conversion.
 */
import { createContext, useContext, type MutableRefObject } from 'react';

const PickedContext = createContext<MutableRefObject<boolean> | null>(null);

export const PickedProvider = PickedContext.Provider;

export function usePickedRef(): MutableRefObject<boolean> {
  const ref = useContext(PickedContext);
  if (!ref) {
    throw new Error('usePickedRef must be used inside a PickedProvider');
  }
  return ref;
}
