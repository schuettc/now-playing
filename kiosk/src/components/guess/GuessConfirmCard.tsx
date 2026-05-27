import { motion } from 'framer-motion';
import type { Guess } from '@/types';
import { TapButton } from '@/components/touch/TapButton';
import { MOTION } from '@/lib/motion';
import { useGuessConfirmHandlers } from './guessConfirmHandlers';
import { primaryButtonLabel, pickManuallyLabel } from './guessCardCopy';

interface Props {
  guess: Guess;
}

/**
 * Inline confirm card for the predicted state.
 *
 * Renders below TrackInfo in TrackLayout's left ShoulderColumn. The
 * card carries:
 *   - 4px amber drain strip on the top edge (60s countdown — no text)
 *   - "BEST GUESS" eyebrow
 *   - Primary CTA with the predicted title baked in ("Yes, that's <title>")
 *   - Ghost button routing to the manual /identify flow
 *
 * No "No" button: there's no defensible "next guess" semantic without a
 * new backend endpoint, and the no-action path is the same as a No tap
 * (the 60s drain runs out, state-decay falls through to NEEDS_ID).
 *
 * See docs/features/inline-confirm-card-predicted/.
 */
export function GuessConfirmCard({ guess }: Props) {
  const { onConfirm, onPickAnother } = useGuessConfirmHandlers(guess, 'card');

  return (
    <motion.div
      data-testid="guess-confirm-card"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: MOTION.chipIn, ease: 'easeOut' }}
      className="flex w-full flex-col overflow-hidden rounded-[14px] backdrop-blur-md"
      style={{
        background: 'var(--sem-warn-tint)',
        border: '1px solid var(--sem-warn-edge)',
      }}
    >
      <GuessDrainStrip />
      <div className="px-5 pt-4 pb-5">
        <span
          className="block font-mono text-[13px] uppercase tracking-[0.32em]"
          style={{ color: 'var(--dot-wait)' }}
        >
          Best guess
        </span>
        <div className="mt-4 flex flex-col gap-2">
          <TapButton intent="primary" className="w-full" onClick={onConfirm}>
            {primaryButtonLabel(guess)}
          </TapButton>
          <TapButton intent="ghost" className="w-full" onClick={onPickAnother}>
            {pickManuallyLabel()}
          </TapButton>
        </div>
      </div>
    </motion.div>
  );
}

function GuessDrainStrip() {
  return (
    <div
      className="h-[4px] origin-left"
      style={{
        backgroundColor: 'var(--dot-wait)',
        animation: `guessConfirmDrain ${MOTION.guessTimeoutMs}ms linear forwards`,
      }}
    />
  );
}
