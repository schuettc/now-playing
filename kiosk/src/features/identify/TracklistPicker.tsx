import { AnimatePresence, motion } from 'framer-motion';
import { TrackPickButton } from './TrackPickButton';
import type { SearchRelease, SearchTrack } from './types';

interface Props {
  rel: SearchRelease;
  tracks: SearchTrack[];
  expanded: boolean;
  highlightedTrackPosition: string | null;
  submittingTrackKey: string | null;
  onTrackPick: (rel: SearchRelease, t: SearchTrack) => void;
}

/**
 * Animated, expandable list of tracks for a single album card. Shows
 * position, title, and (when picked) a "Saving…" state. The token-
 * match autopilot's highlighted track gets an amber border.
 */
export function TracklistPicker({
  rel,
  tracks,
  expanded,
  highlightedTrackPosition,
  submittingTrackKey,
  onTrackPick,
}: Props) {
  const hasTracks = tracks.length > 0;

  return (
    <AnimatePresence initial={false}>
      {expanded && hasTracks && (
        <motion.div
          key="tracks"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="overflow-hidden border-t border-[#1f1f25]"
        >
          <div className="flex flex-col gap-2 p-4">
            {tracks.map((t) => (
              <TrackPickButton
                key={`${rel.release_id}-${t.position ?? t.title}`}
                rel={rel}
                track={t}
                submittingTrackKey={submittingTrackKey}
                highlightedTrackPosition={highlightedTrackPosition}
                onTrackPick={onTrackPick}
              />
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
