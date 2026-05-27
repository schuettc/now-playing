import { motion } from 'framer-motion';
import type { NowPlaying } from '@/types';

interface Props {
  data: NowPlaying;
  count: number;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Pre-computed strings so the JSX body stays simple. */
export function clearFingerprintsCopy(data: NowPlaying, count: number, busy: boolean) {
  return {
    title: data.title ?? 'this track',
    position: data.track_position ? ` (${data.track_position})` : '',
    body:
      `Delete the ${count} fingerprint${count === 1 ? '' : 's'} the kiosk` +
      ` learned for this track. It will re-learn next time you play it.` +
      ` This can't be undone.`,
    confirmLabel: busy ? 'Forgetting…' : 'Forget them',
  };
}

/**
 * Destructive-action confirm sheet for the SomethingWrongPicker's
 * clear-fingerprints row. The "Forget them" CTA is the only ≥44px
 * target; Cancel is smaller — per the destructive-action UX pattern,
 * the safe path is the one you tap deliberately, not the one your
 * thumb lands on by accident.
 *
 * Renders the learned-fingerprint count + the track title verbatim so
 * the user can confirm they're clearing the right cohort, not just
 * "some fingerprints somewhere."
 */
export function ClearFingerprintsConfirm({
  data,
  count,
  busy,
  onConfirm,
  onCancel,
}: Props) {
  const copy = clearFingerprintsCopy(data, count, busy);
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
      onClick={onCancel}
      data-testid="clear-fingerprints-confirm"
      className="fixed inset-0 z-[55] flex items-center justify-center bg-black/80 backdrop-blur-sm"
    >
      <motion.div
        initial={{ scale: 0.96, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.96, opacity: 0 }}
        transition={{ duration: 0.18, ease: 'easeOut' }}
        onClick={(e) => e.stopPropagation()}
        className="flex w-full max-w-md flex-col gap-5 rounded-2xl bg-zinc-900/95 p-8 ring-1 ring-white/10"
      >
        <div className="flex flex-col gap-1">
          <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-rose-300/80">
            Forget fingerprints?
          </div>
          <div className="text-lg font-semibold text-white">
            {copy.title}
            {copy.position}
          </div>
        </div>
        <p className="text-sm text-white/70">{copy.body}</p>
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={onCancel}
            disabled={busy}
            className="font-mono text-[11px] uppercase tracking-[0.3em] text-white/40 transition hover:text-white/70 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            data-testid="clear-fingerprints-confirm-button"
            className="min-h-[44px] rounded-xl bg-rose-500/25 px-6 py-3 text-sm font-semibold text-white ring-1 ring-rose-400/40 transition hover:bg-rose-500/40 disabled:opacity-60"
          >
            {copy.confirmLabel}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
