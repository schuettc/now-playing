import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';

// Maximum time we'll wait for the new image's `onLoad` before snapping it in
// without a crossfade. Cold cache + slow network shouldn't trap us holding
// the previous album's art indefinitely.
const ONLOAD_TIMEOUT_MS = 2000;

interface Layer {
  identity: string;
  src?: string;
}

export interface CrossfadePair {
  current: Layer;
  previous: Layer | null;
  currentReady: boolean;
  handleReady: () => void;
  clearPrevious: () => void;
}

/** Clear any pending fallback timer on the ref. */
function clearFallbackTimer(ref: MutableRefObject<ReturnType<typeof setTimeout> | null>): void {
  if (ref.current !== null) {
    clearTimeout(ref.current);
    ref.current = null;
  }
}

/** Start the fallback snap-in timer; calls onTimeout after ONLOAD_TIMEOUT_MS. */
function startFallbackTimer(
  ref: MutableRefObject<ReturnType<typeof setTimeout> | null>,
  onTimeout: () => void,
): void {
  clearFallbackTimer(ref);
  ref.current = setTimeout(() => {
    ref.current = null;
    onTimeout();
  }, ONLOAD_TIMEOUT_MS);
}

export function useCrossfadePair(identity: string, src?: string): CrossfadePair {
  const [current, setCurrent] = useState<Layer>({ identity, src });
  const [previous, setPrevious] = useState<Layer | null>(null);
  const [currentReady, setCurrentReady] = useState(false);
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (identity === current.identity) {
      if (src !== current.src) setCurrent((c) => ({ ...c, src }));
      return;
    }
    setPrevious(currentReady ? current : null);
    setCurrent({ identity, src });
    setCurrentReady(false);
    startFallbackTimer(fallbackTimerRef, () => {
      setCurrentReady(true);
      setPrevious(null);
    });
    return () => clearFallbackTimer(fallbackTimerRef);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity]);

  const handleReady = useCallback(() => {
    if (fallbackTimerRef.current !== null) {
      clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
    setCurrentReady(true);
  }, []);

  const clearPrevious = useCallback(() => setPrevious(null), []);

  return { current, previous, currentReady, handleReady, clearPrevious };
}
