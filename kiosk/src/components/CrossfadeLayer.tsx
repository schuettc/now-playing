import { motion } from 'framer-motion';
import { useEffect, useReducer } from 'react';
import { NoArtFallback } from './NoArtFallback';
import {
  artLoadReducer,
  initialArtLoad,
  loadingUrlOf,
  retryDelayOf,
  showsNoArt,
} from './artLoad';

interface Props {
  src: string;
  alt: string;
  isReady: boolean;
  onReady: () => void;
}

/**
 * One art layer. Each URL is loaded off-screen first, so the visible <img>
 * only ever gets a URL that has loaded: a new `src` swaps in place with no
 * fade, and a URL that keeps failing ends on the NO ART tile.
 */
export function CrossfadeLayer({ src, alt, isReady, onReady }: Props) {
  const [state, dispatch] = useReducer(artLoadReducer, src, initialArtLoad);
  const loadingUrl = loadingUrlOf(state);
  const retryDelay = retryDelayOf(state);
  const noArt = showsNoArt(state);

  useEffect(() => {
    dispatch({ type: 'target', src });
  }, [src]);

  useEffect(() => {
    if (loadingUrl === null) return;
    const img = new Image();
    img.onload = () => dispatch({ type: 'loaded', url: loadingUrl });
    img.onerror = () => dispatch({ type: 'error', url: loadingUrl });
    img.src = loadingUrl;
    return () => {
      img.onload = null;
      img.onerror = null;
    };
  }, [loadingUrl]);

  useEffect(() => {
    if (retryDelay === null) return;
    const timer = setTimeout(() => dispatch({ type: 'retry' }), retryDelay);
    return () => clearTimeout(timer);
  }, [retryDelay, state.target]);

  // The tile counts as ready so the previous album's layer fades out.
  useEffect(() => {
    if (noArt) onReady();
  }, [noArt, onReady]);

  if (noArt) return <NoArtFallback />;
  if (state.shown === null) return null;
  return (
    <motion.img
      src={state.shown}
      alt={alt}
      draggable={false}
      initial={{ opacity: 0 }}
      animate={{ opacity: isReady ? 1 : 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      onLoad={onReady}
      className="absolute inset-0 h-full w-full object-cover"
      style={{ willChange: 'opacity' }}
    />
  );
}
