import { motion } from 'framer-motion';
import { AlbumThumb } from './AlbumThumb';
import { buildHeaderStyles } from './albumCardHeaderHelpers';
import type { SearchRelease } from './types';

interface Props {
  rel: SearchRelease;
  expanded: boolean;
  hasTracks: boolean;
  captionParts: string[];
  onToggleExpanded: () => void;
}

/**
 * Header button for an `AlbumCard` — thumb + title/artist/caption.
 * The inner `motion.div layout="position"` wraps only the text block,
 * not the whole header, so Framer Motion's layout animation continues
 * to track a stable subtree across the collapsed/expanded transition.
 */
export function AlbumCardHeader({
  rel,
  expanded,
  hasTracks,
  captionParts,
  onToggleExpanded,
}: Props) {
  const s = buildHeaderStyles(expanded, hasTracks);
  return (
    <button
      type="button"
      onClick={onToggleExpanded}
      disabled={!hasTracks}
      style={{ touchAction: 'manipulation' }}
      className={s.button}
    >
      <AlbumThumb rel={rel} size={expanded ? 'large' : 'square'} />
      <motion.div layout="position" className={s.textBlock}>
        <div className={s.title} style={s.titleStyle}>
          {rel.title}
        </div>
        <div className={s.artist}>{rel.artist}</div>
        {captionParts.length > 0 && (
          <div className={s.caption}>{captionParts.join(' · ')}</div>
        )}
      </motion.div>
    </button>
  );
}
