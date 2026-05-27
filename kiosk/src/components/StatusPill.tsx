import type { IdentifyState, Source } from '@/types';

interface Props {
  source: Source;
  identifyState: IdentifyState;
  /** When provided, the pill renders as a button with a trailing
      "Something wrong?" segment and becomes the picker entry point. */
  onTap?: () => void;
}

interface PillContent {
  label: string;
  sub: string;
  dot: string; /* CSS color */
  pulse: boolean;
}

const SOURCE_LABELS: Record<Source, string> = {
  vinyl: 'VINYL',
  streaming: 'STREAMING',
  radio: 'RADIO',
  airplay: 'AIRPLAY',
  tv: 'TV',
  unknown: '—',
};

function sourceLabelFor(source: Source): string {
  return SOURCE_LABELS[source];
}

/**
 * Map (source, identifyState) to the pill's five canonical content
 * shapes per the design spec.
 *
 * Anatomy (per design Surface 4): dot (color = across-the-room read)
 * + source label · state qualifier + separator + mono sub (up-close).
 * The `awaiting-confirm` state expands the label to
 * `<SRC> · BEST GUESS` so the across-the-room read makes the wait
 * intent legible without the user needing to look at the sub.
 *
 * Per design decision: ONLY `awaiting-confirm` pulses. Every other
 * state holds steady so the kiosk reads as calm from across the
 * room.
 */
type StateEntry = {
  sub: string;
  dot: string;
  pulse: boolean;
  /** When set, suffix this onto the source-derived label
      (e.g. `VINYL` + ` · BEST GUESS`). */
  labelSuffix?: string;
  /** When set, replace the source-derived label entirely. */
  fullLabel?: string;
};

const STATE_LOOKUP: Record<IdentifyState, StateEntry> = {
  'confirmed-shazam': {
    sub: 'Shazam · matched',
    dot: 'var(--dot-ok)',
    pulse: false,
  },
  'confirmed-local': {
    sub: 'Fingerprint · remembered',
    dot: 'var(--dot-ok)',
    pulse: false,
  },
  'awaiting-confirm': {
    // Why: the inline confirm card below TrackInfo carries the affordance
    // and the title. A pill sub here would just duplicate what the card
    // already shows; the dot + label is enough to communicate state.
    sub: '',
    dot: 'var(--dot-wait)',
    pulse: true,
    labelSuffix: ' · BEST GUESS',
  },
  'identifying': {
    sub: 'identifying…',
    dot: 'var(--dot-wait)',
    pulse: true,
  },
  'user-pinned': {
    sub: 'learning…',
    dot: 'var(--dot-user)',
    pulse: false,
    fullLabel: 'JUST CONFIRMED',
  },
  'needs-id': {
    sub: 'Unknown · help identify',
    dot: 'var(--dot-idle)',
    pulse: false,
  },
};

// Exported for unit-testing the state-to-content table without
// React-render dependencies. Consumers should prefer rendering
// `<StatusPill />` directly.
export function pillContent(
  source: Source,
  identifyState: IdentifyState,
): PillContent {
  const entry = STATE_LOOKUP[identifyState];
  const baseLabel = sourceLabelFor(source);
  const label = entry.fullLabel ?? `${baseLabel}${entry.labelSuffix ?? ''}`;
  // For non-vinyl sources, suppress the vinyl-cascade method label
  // (Shazam/Fingerprint/learning…/help identify). The kiosk doesn't
  // run the cascade for AirPlay or streaming — Sonos provides the
  // metadata directly, so claiming "Shazam · matched" is misleading.
  // Vinyl keeps the full label.
  const sub = source === 'vinyl' ? entry.sub : '';
  return { label, sub, dot: entry.dot, pulse: entry.pulse };
}

/**
 * Unified source-and-confidence chip. Five identify states; only
 * `awaiting-confirm` animates.
 *
 * Position: anchored by the parent (`StatusOverlay` puts it at
 * top:32, right:40 of the stage). Pill itself is intrinsically
 * sized.
 *
 * See docs/features/confirmed-fingerprint-coverage/design-output/
 * README.md § "Surface 4 — Unified StatusPill".
 */
export function StatusPill({ source, identifyState, onTap }: Props) {
  const content = pillContent(source, identifyState);
  const isInteractive = typeof onTap === 'function';
  const className =
    'flex min-h-[44px] items-center gap-3 rounded-full px-4 py-2 backdrop-blur-md text-left' +
    (isInteractive
      ? ' transition hover:bg-white/5 hover:ring-white/30'
      : '');
  const body = (
    <>
      <span
        className="h-2 w-2 rounded-full"
        style={{
          backgroundColor: content.dot,
          boxShadow: `0 0 12px ${content.dot}`,
          animation: content.pulse ? 'statusPulse 1.6s ease-out infinite' : 'none',
        }}
      />
      <span
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--text-body)' }}
      >
        {content.label}
      </span>
      {content.sub && (
        <>
          <Divider />
          <span
            className="font-mono text-[10px] tracking-[0.15em]"
            style={{ color: 'var(--text-secondary)' }}
          >
            {content.sub}
          </span>
        </>
      )}
      {isInteractive && (
        <>
          <Divider />
          <span
            className="font-mono text-[10px] tracking-[0.15em]"
            style={{ color: 'var(--text-tertiary)' }}
          >
            Something wrong?
          </span>
        </>
      )}
    </>
  );
  const baseStyle = {
    background: 'rgba(0,0,0,0.35)',
    border: '1px solid var(--text-hairline)',
  } as const;
  if (isInteractive) {
    return (
      <button
        type="button"
        onClick={onTap}
        data-testid="status-pill"
        data-state={identifyState}
        aria-label="Something wrong?"
        className={className}
        style={baseStyle}
      >
        {body}
      </button>
    );
  }
  return (
    <div
      data-testid="status-pill"
      data-state={identifyState}
      className={className}
      style={baseStyle}
    >
      {body}
    </div>
  );
}

function Divider() {
  return (
    <span
      aria-hidden="true"
      style={{ color: 'var(--text-quaternary)' }}
      className="font-mono text-[10px]"
    >
      │
    </span>
  );
}

