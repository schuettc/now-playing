import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';

// Maximum time we'll wait for the new image's `onLoad` before snapping it in
// without a crossfade. Cold cache + slow network shouldn't trap us holding
// the previous album's art indefinitely.
const ONLOAD_TIMEOUT_MS = 2000;

export interface Layer {
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

/**
 * A changed `src` under an unchanged identity (a per-track art URL on the
 * same album, or art arriving after a payload with none) updates the layer
 * in place: no crossfade, and the layer keeps its loaded image until the new
 * one is ready.
 */
export function swapSrcInPlace(layer: Layer, identity: string, src?: string): Layer {
  if (layer.identity !== identity || layer.src === src) return layer;
  return { ...layer, src };
}

export function useCrossfadePair(identity: string, src?: string): CrossfadePair {
  const [current, setCurrent] = useState<Layer>({ identity, src });
  const [previous, setPrevious] = useState<Layer | null>(null);
  const [currentReady, setCurrentReady] = useState(false);
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Identity change: crossfade from the old layer to a new one. Reads the
  // state as of the render where identity changed, so it is keyed on
  // identity alone.
  useEffect(() => {
    if (identity === current.identity) return;
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

  // Runs after the identity effect, so on an identity change the updater
  // sees the new layer and leaves it alone.
  useEffect(() => {
    setCurrent((c) => swapSrcInPlace(c, identity, src));
  }, [identity, src]);

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
