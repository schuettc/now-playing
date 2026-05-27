import { AnimatePresence, motion } from 'framer-motion';
import { CrossfadeLayer } from './CrossfadeLayer';
import { useCrossfadePair } from './useCrossfadePair';

interface Props {
  src?: string;
  alt?: string;
  identity: string;
  dim?: boolean;
}

/** Animate target values that differ only by the `dim` flag. */
function buildDimAnimate(dim: boolean) {
  return { opacity: dim ? 0.35 : 1, scale: 1, filter: dim ? 'saturate(0.5)' : 'saturate(1)' };
}

function NoArtFallback() {
  return (
    <div className="flex h-full w-full items-center justify-center bg-zinc-900 text-zinc-500">
      <span className="font-mono text-sm tracking-widest">NO ART</span>
    </div>
  );
}

function CurrentArtLayer({
  identity,
  src,
  alt,
  isReady,
  onReady,
}: {
  identity: string;
  src: string | undefined;
  alt: string;
  isReady: boolean;
  onReady: () => void;
}) {
  if (!src) return <NoArtFallback />;
  return (
    <CrossfadeLayer
      key={identity}
      src={src}
      alt={alt}
      isReady={isReady}
      onReady={onReady}
    />
  );
}

export function AlbumArt({ src, alt, identity, dim = false }: Props) {
  const { current, previous, currentReady, handleReady, clearPrevious } =
    useCrossfadePair(identity, src);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96, filter: 'saturate(1)' }}
      animate={buildDimAnimate(dim)}
      transition={{ duration: 0.5, ease: 'easeOut' }}
      style={{ willChange: 'opacity, transform, filter' }}
      className="relative aspect-square h-[85vh] max-h-[840px] w-auto shrink-0 overflow-hidden rounded-sm shadow-[0_30px_80px_-20px_rgba(0,0,0,0.8)] ring-1 ring-white/5"
    >
      <AnimatePresence>
        {previous?.src && (
          <PreviousLayer
            identity={previous.identity}
            src={previous.src}
            fading={currentReady}
            onFadedOut={clearPrevious}
          />
        )}
      </AnimatePresence>
      <CurrentArtLayer
        identity={current.identity}
        src={current.src}
        alt={alt ?? ''}
        isReady={currentReady}
        onReady={handleReady}
      />
    </motion.div>
  );
}

function PreviousLayer({
  identity, src, fading, onFadedOut,
}: {
  identity: string;
  src: string;
  fading: boolean;
  onFadedOut: () => void;
}) {
  return (
    <motion.img
      key={`prev-${identity}`}
      src={src}
      alt=""
      draggable={false}
      initial={{ opacity: 1 }}
      animate={{ opacity: fading ? 0 : 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      onAnimationComplete={() => { if (fading) onFadedOut(); }}
      className="absolute inset-0 h-full w-full object-cover"
      style={{ willChange: 'opacity' }}
    />
  );
}
