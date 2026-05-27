import { AnimatePresence } from 'framer-motion';
import { BlurredBackdrop } from '@/components/BlurredBackdrop';
import { withCacheBust } from '@/lib/art';

interface Props {
  show: boolean;
  artId: string;
  artCacheBust: number;
  effectiveArtUrl: string | undefined;
  isPaused: boolean;
}

// No mode="wait" on the layered animations so old and new crossfade
// through each other (mode="wait" creates a visible gap between
// exit and enter that reads as a hard cut).
export function TrackBackdrop({
  show,
  artId,
  artCacheBust,
  effectiveArtUrl,
  isPaused,
}: Props) {
  return (
    <AnimatePresence>
      {show && (
        <BlurredBackdrop
          key={`bg-${artId}-${artCacheBust}`}
          src={withCacheBust(effectiveArtUrl, artCacheBust)}
          identity={`${artId}#${artCacheBust}`}
          dim={isPaused}
        />
      )}
    </AnimatePresence>
  );
}
