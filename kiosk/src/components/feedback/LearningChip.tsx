import { useEffect } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useStore } from '@/store/useStore';
import { MOTION } from '@/lib/motion';

/**
 * Bottom-center auto-dismissing toast that appears after a confirmation
 * tap. Quiet ack only — never demands engagement.
 *
 * Driven by `state.learningChipPulses` (a monotonic counter). Surfaces
 * that complete a confirmation tap call `pulseLearningChip()` to
 * fire the chip. Counter-as-key means a rapid second tap cancel-and-
 * replaces the current instance via `AnimatePresence`.
 *
 * Suppressed for non-vinyl sources: the "learning" concept is specific
 * to the local fingerprint DB. AirPlay/streaming tracks are identified
 * by Sonos — there is nothing to learn.
 *
 * See `docs/features/confirmed-fingerprint-coverage/design-output/`
 * § "Surface 5 — Learning chip".
 */
export function LearningChip() {
  const pulses = useStore((s) => s.learningChipPulses);
  const source = useStore((s) => s.payload?.source);
  const isVinyl = source === 'vinyl';
  return (
    <AnimatePresence>
      {pulses > 0 && isVinyl && <LearningChipInstance key={pulses} />}
    </AnimatePresence>
  );
}

/** Animated pill that auto-dismisses after MOTION.learningChipMs. */
function LearningChipInstance() {
  // The parent re-mounts (new key from pulses counter) on each fire,
  // so a stale timer never lingers past its instance.
  const pulses = useStore((s) => s.learningChipPulses);
  const setPulses = useStore.setState;
  useEffect(() => {
    const cur = pulses;
    const t = setTimeout(() => {
      // Only clear if no new pulse came in while we were waiting.
      // setState callback form keeps this atomic against rapid taps.
      setPulses((s) =>
        s.learningChipPulses === cur ? { learningChipPulses: 0 } : s,
      );
    }, MOTION.learningChipMs);
    return () => clearTimeout(t);
  }, [pulses, setPulses]);

  return (
    <motion.div
      role="status"
      aria-live="polite"
      // Combine horizontal centering (`x: '-50%'`) with vertical entry
      // animation inside Framer Motion's managed transform so motion
      // doesn't overwrite a manual translateX style.
      initial={{ opacity: 0, y: 8, x: '-50%' }}
      animate={{ opacity: 1, y: 0, x: '-50%' }}
      exit={{ opacity: 0, y: 8, x: '-50%' }}
      transition={{ duration: MOTION.chipIn, ease: 'easeOut' }}
      className="fixed left-1/2 z-[25] flex items-center gap-2 rounded-full px-[18px] py-3 backdrop-blur-md"
      style={{ bottom: 72, background: 'rgba(0,0,0,0.6)', border: '1px solid var(--sem-ok-edge)' }}
    >
      <LearningChipContent />
    </motion.div>
  );
}

function LearningChipContent() {
  return (
    <>
      <span
        className="h-2 w-2 rounded-full"
        style={{ backgroundColor: 'var(--dot-ok)', boxShadow: '0 0 8px var(--dot-ok)' }}
      />
      <span
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--text-body)' }}
      >
        Learning this track
      </span>
      <span aria-hidden="true" style={{ color: 'var(--text-quaternary)' }} className="text-[10px]">
        ·
      </span>
      <span
        className="font-mono text-[10px] tracking-[0.15em]"
        style={{ color: 'var(--text-secondary)' }}
      >
        we'll know it next time
      </span>
    </>
  );
}
