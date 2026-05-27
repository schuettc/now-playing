import { useEffect } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useStore } from '@/store/useStore';
import { MOTION } from '@/lib/motion';
import { inlineErrorCaption } from './inlineErrorHelpers';

/**
 * Transient error strip anchored under the top-right StatusPill
 * cluster (`top: 88px`, `right: 40px`). Briefly surfaces a
 * `/api/pin-track` or `/control/select-release` 4xx / timeout /
 * network failure, then auto-dismisses after `MOTION.inlineErrorMs`
 * (6s).
 *
 * Mounted always; gates on `state.pinErrorReason !== null`.
 * `usePinTrack` sets the slice on failure; this component clears
 * it via the timer.
 *
 * Spec: docs/features/identify-confirm-first-ux/plan.md
 * § "Inline-error UX".
 */
export function InlineError() {
  const reason = useStore((s) => s.pinErrorReason);
  const clear = useStore((s) => s.setPinErrorReason);

  useEffect(() => {
    if (reason === null) return;
    const t = setTimeout(() => clear(null), MOTION.inlineErrorMs);
    return () => clearTimeout(t);
  }, [reason, clear]);

  return (
    <AnimatePresence>
      {reason !== null && (
        <motion.div
          key={reason}
          role="alert"
          aria-live="assertive"
          data-testid="inline-error"
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: MOTION.chipIn, ease: 'easeOut' }}
          className="fixed z-[60]"
          style={{
            top: 88,
            right: 40,
            color: 'var(--sem-danger)',
          }}
        >
          <span className="font-mono text-[11px] uppercase tracking-[0.3em]">
            {inlineErrorCaption(reason)}
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
