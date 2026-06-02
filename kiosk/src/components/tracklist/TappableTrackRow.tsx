import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { formatDuration } from '@/utils/format';
import { getBodyClass, getPositionClass, getDurationClass } from '@/components/TrackRow';
import { usePinTrack } from '@/hooks/usePinTrack';
import { MOTION } from '@/lib/motion';
import { track as telemetryTrack } from '@/lib/telemetry';
import { rowState, type FlashState, type TapRowState } from './tapState';

// Layout id distinct from `TrackRow`'s so the tappable variant's
// highlight doesn't try to animate across panels on source flips.
const TAPPABLE_LAYOUT_ID = 'tracklist-tappable-current';

const HIGHLIGHT_TRANSITION = {
  type: 'spring' as const,
  stiffness: 300,
  damping: 30,
};

interface Props {
  releaseId: number;
  position: string;
  title: string;
  durationSeconds?: number | null;
  currentPosition: string | null | undefined;
  guessPosition: string | null | undefined;
  /** Backend guess confidence — styles the guess ring (weaker as it decays). */
  guessConfidence?: 'high' | 'medium' | 'low' | null;
  peek?: boolean;
  variant?: 'subtle';
  /**
   * True (default) when this row belongs to the currently-locked album.
   * When true, tapping calls `/api/pin-track` (fast path).
   * When false, tapping calls `/api/identify` (accepts any catalog release).
   * Pass `false` for past-album scoped views reached via the recents hero.
   */
  isCurrentAlbum?: boolean;
}

type GuessConfidence = 'high' | 'medium' | 'low' | null | undefined;

// Guess ring styling by backend confidence (epic consolidate-guess-confidence-
// lifetime): the amber circle visibly weakens as the lock's confidence decays —
// solid+brighter at high, faint dashed at low. Undefined → medium (legacy look).
const GUESS_RING: Record<'high' | 'medium' | 'low', { background: string; border: string }> = {
  high: { background: 'rgba(242,194,102,0.12)', border: '1px solid var(--sem-warn-edge)' },
  medium: { background: 'rgba(242,194,102,0.06)', border: '1px dashed var(--sem-warn-edge)' },
  low: { background: 'rgba(242,194,102,0.03)', border: '1px dashed rgba(242,194,102,0.40)' },
};

// Persistent state layer — current pill (with shared-layout id so it
// slides between rows on track change), guess dashed ring, or nothing.
function PersistentLayer(
  { persistent, guessConfidence }: { persistent: TapRowState; guessConfidence?: GuessConfidence },
) {
  if (persistent === 'current') {
    return (
      <motion.div
        layoutId={TAPPABLE_LAYOUT_ID}
        transition={HIGHLIGHT_TRANSITION}
        className="absolute inset-0 -z-10 rounded-sm"
        style={{
          background: 'rgba(255,255,255,0.12)',
          border: '1px solid rgba(255,255,255,0.22)',
        }}
      />
    );
  }
  if (persistent === 'guess') {
    return (
      <div
        className="absolute inset-0 -z-10 rounded-sm"
        style={GUESS_RING[guessConfidence ?? 'medium']}
      />
    );
  }
  return null;
}

// Flash layer — green tint over the persistent layer during the
// 1400ms just-tapped state. Layered ABOVE the persistent layer
// (z-index numerically higher / not -z-10) so the shared-layout
// pill keeps sliding to the new row behind the green glow.
// impl-review-1 fix: was previously exclusive with persistent.
function FlashLayer() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="pointer-events-none absolute inset-0 rounded-sm"
      style={{
        background: 'var(--sem-ok-tint)',
        border: '1px solid var(--sem-ok-edge)',
        zIndex: -5,
      }}
    />
  );
}

function RowOverlay({
  persistent, flash, guessConfidence,
}: { persistent: TapRowState; flash: FlashState; guessConfidence?: GuessConfidence }) {
  return (
    <>
      <PersistentLayer persistent={persistent} guessConfidence={guessConfidence} />
      {flash === 'just-tapped' && <FlashLayer />}
    </>
  );
}

function TrailingMarker({
  persistent, flash,
}: { persistent: TapRowState; flash: FlashState }) {
  if (flash === 'just-tapped') {
    return (
      <span
        className="ml-1 font-mono text-[10px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--dot-ok)' }}
      >
        locked ✓
      </span>
    );
  }
  if (persistent === 'current') {
    return (
      <span
        aria-hidden="true"
        className="ml-1 h-2 w-2 rounded-full"
        style={{
          backgroundColor: 'var(--dot-ok)',
          boxShadow: '0 0 6px var(--dot-ok)',
        }}
      />
    );
  }
  if (persistent === 'guess') {
    return (
      <span
        className="ml-1 font-mono text-[10px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--dot-wait)' }}
      >
        guess
      </span>
    );
  }
  return null;
}

function RowContent({
  persistent, position, title, durationSeconds, peek,
}: {
  persistent: TapRowState;
  position: string;
  title: string;
  durationSeconds?: number | null;
  peek: boolean;
}) {
  const isCurrent = persistent === 'current';
  const isGuess = persistent === 'guess';
  return (
    <>
      <span
        className={`w-9 shrink-0 font-mono text-[15px] tabular-nums ${getPositionClass(isCurrent, peek, false)}`}
        style={isGuess ? { color: 'var(--dot-wait)' } : undefined}
      >
        {position}
      </span>
      <span className={`min-w-0 flex-1 truncate ${isCurrent ? 'font-medium' : ''}`}>
        {title}
      </span>
      <span
        className={`shrink-0 font-mono text-[14px] tabular-nums ${getDurationClass(peek, false)}`}
      >
        {formatDuration(durationSeconds ?? null)}
      </span>
    </>
  );
}

/**
 * Tappable variant of `TrackRow` for the locked-vinyl tracklist.
 * See plan.md and design Surface 2 for the state matrix.
 */
/** Manages the short 'just-tapped' flash state with auto-reset. */
function useFlash(): [FlashState, () => void] {
  const [flash, setFlash] = useState<FlashState>('idle');
  useEffect(() => {
    if (flash !== 'just-tapped') return;
    const t = setTimeout(() => setFlash('idle'), MOTION.confirmFlashMs);
    return () => clearTimeout(t);
  }, [flash]);
  return [flash, () => setFlash('just-tapped')];
}

export function TappableTrackRow({
  releaseId,
  position,
  title,
  durationSeconds,
  currentPosition,
  guessPosition,
  guessConfidence,
  peek = false,
  variant = 'subtle',
  isCurrentAlbum = true,
}: Props) {
  const persistent = rowState({ position, currentPosition, guessPosition });
  const [flash, triggerFlash] = useFlash();
  const pinTrack = usePinTrack();

  const isCurrent = persistent === 'current';
  const isGuess = persistent === 'guess';

  const handleTap = () => {
    triggerFlash();
    telemetryTrack('identify_tracklist_tap', {
      variant,
      was_current_row: isCurrent,
      was_guess_row: isGuess,
    });
    void pinTrack({
      release_id: releaseId,
      track_position: position,
      entry: 'tracklist',
      isCurrentAlbum,
    });
  };

  return (
    <motion.button
      type="button"
      onClick={handleTap}
      whileTap={{ scale: 0.99 }}
      transition={{ duration: MOTION.buttonPress, ease: 'easeOut' }}
      className={`relative flex w-full items-baseline gap-3.5 rounded-sm px-2 py-2 text-left text-2xl leading-tight ${getBodyClass(isCurrent, peek, false)}`}
      aria-pressed={isCurrent}
      data-testid={`tappable-row-${position}`}
      data-row-state={persistent}
    >
      <RowOverlay persistent={persistent} flash={flash} guessConfidence={guessConfidence} />
      <RowContent
        persistent={persistent}
        position={position}
        title={title}
        durationSeconds={durationSeconds}
        peek={peek}
      />
      <TrailingMarker persistent={persistent} flash={flash} />
    </motion.button>
  );
}
