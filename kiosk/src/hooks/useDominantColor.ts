import { useEffect, useState } from 'react';
import { pickDominantColor, sampleImageData, type RGB } from './dominantColor';

export type { RGB } from './dominantColor';

/** Extract the dominant color from an already-loaded HTMLImageElement. */
function extractColor(img: HTMLImageElement): RGB | null {
  try {
    const data = sampleImageData(img);
    return data ? pickDominantColor(data) : null;
  } catch {
    // CORS or canvas-tainting → leave color null
    return null;
  }
}

/**
 * Load an image and extract its dominant color, calling `onColor` with the
 * result. Returns a cleanup function that cancels the async work.
 */
function loadImageColor(
  src: string,
  onColor: (c: RGB | null) => void,
): () => void {
  let cancelled = false;
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    if (cancelled) return;
    const best = extractColor(img);
    if (best) onColor(best);
  };
  img.onerror = () => {
    if (!cancelled) onColor(null);
  };
  img.src = src;
  return () => { cancelled = true; };
}

/**
 * Loads `src` into an offscreen canvas, downscales to 32x32, scans pixels for
 * the one with the highest saturation × mid-lightness score (filtering
 * near-black and near-white), and returns its RGB. Returns null on load
 * failure / CORS error.
 */
export function useDominantColor(src: string | undefined): RGB | null {
  const [color, setColor] = useState<RGB | null>(null);

  useEffect(() => {
    if (!src) {
      setColor(null);
      return;
    }
    return loadImageColor(src, setColor);
  }, [src]);

  return color;
}
