import { motion } from 'framer-motion';
import type { AlternateRelease, NowPlaying } from '@/types';
import { AlternatesList } from './AlternatesList';

interface Props {
  data: NowPlaying;
  alternates: AlternateRelease[];
  onClose: () => void;
  onSelect: (
    release_id: number,
    track_position?: string,
    track_title?: string,
  ) => void;
}

/**
 * Full-screen modal that hosts the AlternatesList for the
 * SomethingWrongPicker's "Wrong album" row. Same overlay pattern as
 * ArtPicker — the user is fully in this view until they pick an
 * alternate or close.
 */
export function AlternatesModal({ data, alternates, onClose, onSelect }: Props) {
  const subline = data.title ?? '';
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
      onClick={onClose}
      data-testid="alternates-modal"
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm"
    >
      <motion.div
        initial={{ y: 80 }}
        animate={{ y: 0 }}
        exit={{ y: 80 }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
        onClick={(e) => e.stopPropagation()}
        className="m-12 flex max-h-[80vh] w-full max-w-2xl flex-col gap-6 overflow-y-auto rounded-2xl bg-zinc-900/90 p-10 ring-1 ring-white/10"
      >
        <div className="flex flex-col gap-1">
          <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-white/40">
            Wrong album?
          </div>
          <div className="text-2xl font-semibold text-white">
            Same track, different pressing
          </div>
          {subline && (
            <div className="text-sm text-white/40">
              Now playing: {subline}
            </div>
          )}
        </div>

        <AlternatesList alternates={alternates} onSelect={onSelect} />

        <button
          onClick={onClose}
          className="self-end font-mono text-[11px] uppercase tracking-[0.3em] text-white/40 transition hover:text-white/70"
        >
          Close
        </button>
      </motion.div>
    </motion.div>
  );
}
