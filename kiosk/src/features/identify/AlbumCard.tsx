import { motion } from 'framer-motion';
import { AlbumCardCloseButton } from './AlbumCardCloseButton';
import { AlbumCardHeader } from './AlbumCardHeader';
import { buildCaptionParts } from './albumCardHelpers';
import { TracklistPicker } from './TracklistPicker';
import { useAlbumCardScroll } from './useAlbumCardScroll';
import type { SearchRelease, SearchTrack } from './types';

interface Props {
  rel: SearchRelease;
  expanded: boolean;
  highlightedTrackPosition: string | null;
  submittingTrackKey: string | null;
  onToggleExpanded: () => void;
  onTrackPick: (rel: SearchRelease, t: SearchTrack) => void;
}

/** Tailwind border + layout class for the card depending on expanded state. */
function resolveCardBorderClass(expanded: boolean): string {
  return expanded
    ? 'col-span-full border-[#6e8aff]'
    : 'border-[#1f1f25] hover:border-[#2c2c34]';
}

/**
 * Result-grid card for a single album/release. Collapsed state shows
 * just the thumb + caption; expanded state spans the full grid width,
 * shows the tracklist (via `TracklistPicker`), and self-scrolls into
 * view past the layout animation.
 */
export function AlbumCard({
  rel,
  expanded,
  highlightedTrackPosition,
  submittingTrackKey,
  onToggleExpanded,
  onTrackPick,
}: Props) {
  const captionParts = buildCaptionParts(rel);
  const hasTracks = (rel.tracks || []).length > 0;
  const cardRef = useAlbumCardScroll(expanded);
  const borderClass = resolveCardBorderClass(expanded);

  return (
    <motion.div
      ref={cardRef}
      layout="position"
      transition={{ duration: 0.12, ease: 'easeOut' }}
      className={`relative flex flex-col overflow-hidden rounded-[14px] border bg-white/[0.02] ${borderClass}`}
    >
      {expanded && <AlbumCardCloseButton onClose={onToggleExpanded} />}
      <AlbumCardHeader
        rel={rel}
        expanded={expanded}
        hasTracks={hasTracks}
        captionParts={captionParts}
        onToggleExpanded={onToggleExpanded}
      />
      <TracklistPicker
        rel={rel}
        tracks={rel.tracks || []}
        expanded={expanded}
        highlightedTrackPosition={highlightedTrackPosition}
        submittingTrackKey={submittingTrackKey}
        onTrackPick={onTrackPick}
      />
    </motion.div>
  );
}
