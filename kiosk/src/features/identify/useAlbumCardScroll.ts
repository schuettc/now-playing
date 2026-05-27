import { useEffect, useRef } from 'react';

/**
 * Delay before scrolling an expanded card into view. Tuned to land
 * past the framer-motion layout (120ms) + tracklist height (200ms)
 * transitions so we scroll to the final bounding rect rather than the
 * mid-animation one.
 */
const SCROLL_DELAY_MS = 260;

export function useAlbumCardScroll(expanded: boolean) {
  const cardRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!expanded) return;
    const el = cardRef.current;
    if (!el) return;
    const id = window.setTimeout(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, SCROLL_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [expanded]);

  return cardRef;
}
