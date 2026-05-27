import { motion } from 'framer-motion';
import { formatDuration } from '@/utils/format';

/**
 * Spring transition used for the shared-layout highlight pill that
 * smoothly slides between current rows when the track changes. Each
 * caller (TracklistPanel, QueuePanel) supplies its own `layoutId` to
 * avoid Framer Motion animating the highlight across unrelated DOM
 * during source-flip transitions.
 */
const HIGHLIGHT_TRANSITION = {
  type: 'spring' as const,
  stiffness: 300,
  damping: 30,
};

/**
 * Compute the body text color based on row state.
 * Tier precedence: current > peek > dimmed > default.
 * Peek sits between current and dimmed because peek rows are "soft preview"
 * not "already played" — same alpha range, different semantics.
 */
export function getBodyClass(
  isCurrent: boolean,
  peek: boolean,
  isDimmed: boolean,
): string {
  if (isCurrent) return 'text-white';
  if (peek) return 'text-white/60';
  if (isDimmed) return 'text-white/30';
  return 'text-white/55';
}

/**
 * Compute the position label color based on row state.
 */
export function getPositionClass(
  isCurrent: boolean,
  peek: boolean,
  isDimmed: boolean,
): string {
  if (isCurrent) return 'text-white/80';
  if (peek) return 'text-white/30';
  if (isDimmed) return 'text-white/20';
  return 'text-white/30';
}

/**
 * Compute the duration text color based on row state.
 */
export function getDurationClass(peek: boolean, isDimmed: boolean): string {
  if (peek || isDimmed) return 'text-white/20';
  return 'text-white/30';
}

interface Props {
  /** Distinct per-panel scope, e.g. "tracklist-current-highlight". */
  layoutId: string;
  /** Position label (vinyl: "A1"/"B2"; streaming queue: "1"/"2"). */
  position: string;
  /** Track title. */
  title: string;
  /** Duration in seconds; omit to render an empty duration cell. */
  durationSeconds?: number | null;
  /** Currently-playing row — gets the highlight pill + emphasised text. */
  isCurrent?: boolean;
  /** "Already played" — dimmed past tracks in the streaming queue. */
  isDimmed?: boolean;
  /**
   * End-of-side peek row (vinyl tracklist). Renders the next side's
   * first track at ~60% alpha so it reads as "preview, not yet
   * playing." Filter logic guarantees `peek` and `isCurrent` are
   * never both true.
   */
  peek?: boolean;
}

/**
 * Shared row template used by `TracklistPanel` (vinyl) and `QueuePanel`
 * (streaming). The row is: position label · title · duration. Current
 * row gets a Framer Motion shared-layout highlight pill that slides
 * between rows when the current track changes.
 */
export function TrackRow({
  layoutId,
  position,
  title,
  durationSeconds,
  isCurrent = false,
  isDimmed = false,
  peek = false,
}: Props) {
  const bodyClass = getBodyClass(isCurrent, peek, isDimmed);
  const positionClass = getPositionClass(isCurrent, peek, isDimmed);
  const titleEmphasis = isCurrent ? 'font-medium' : '';
  const durationClass = getDurationClass(peek, isDimmed);

  return (
    <div
      className={`relative flex items-baseline gap-3.5 rounded-sm px-2 py-2 text-2xl leading-tight ${bodyClass}`}
    >
      {isCurrent && (
        <motion.div
          layoutId={layoutId}
          transition={HIGHLIGHT_TRANSITION}
          className="absolute inset-0 -z-10 rounded-sm bg-white/10 ring-1 ring-white/20"
        />
      )}
      <span
        className={`w-9 shrink-0 font-mono text-[15px] tabular-nums ${positionClass}`}
      >
        {position}
      </span>
      <span className={`min-w-0 flex-1 truncate ${titleEmphasis}`}>
        {title}
      </span>
      <span
        className={`shrink-0 font-mono text-[14px] tabular-nums ${durationClass}`}
      >
        {formatDuration(durationSeconds ?? null)}
      </span>
    </div>
  );
}
