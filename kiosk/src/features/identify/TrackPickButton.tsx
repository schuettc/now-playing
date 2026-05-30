import type { SearchRelease, SearchTrack } from './types';

type BadgeState = 'submitting' | 'highlighted' | 'default';

/** Build the composite key used to match a submitting track. */
function buildTrackKey(releaseId: number, position: string | undefined): string {
  return `${releaseId}-${position ?? ''}`;
}

/** True when this track matches the highlighted position. */
function isTrackHighlighted(
  highlightedPosition: string | null,
  trackPosition: string | undefined,
): boolean {
  return highlightedPosition !== null && trackPosition === highlightedPosition;
}

/** Resolve the badge/border state for a track row from its pick flags. */
function resolveTrackButtonState(
  isSubmitting: boolean,
  isHighlighted: boolean,
): BadgeState {
  if (isSubmitting) return 'submitting';
  if (isHighlighted) return 'highlighted';
  return 'default';
}

/** Map a BadgeState to the Tailwind border+bg classes for the row button. */
function resolveTrackBorderClass(state: BadgeState): string {
  if (state === 'submitting') return 'border-[#6e8aff] bg-[#6e8aff]/[0.18]';
  if (state === 'highlighted') return 'border-[#ffd166] bg-[#ffd166]/[0.10]';
  return 'border-[#1f1f25] bg-transparent hover:border-[#6e8aff] hover:bg-[#6e8aff]/[0.08]';
}

function TrackPositionBadge({
  position,
  state,
}: {
  position: string;
  state: BadgeState;
}) {
  const colorClass =
    state === 'submitting'
      ? 'text-[#6e8aff]'
      : state === 'highlighted'
        ? 'text-[#ffd166]'
        : 'text-[#8a8a95]';
  return (
    <span
      className={`min-w-[36px] shrink-0 font-semibold tabular-nums ${colorClass}`}
    >
      {position}
    </span>
  );
}

function TrackSavingRow({ position }: { position: string }) {
  return (
    <>
      <TrackPositionBadge position={position} state="submitting" />
      <span className="min-w-0 flex-1 truncate text-[#6e8aff]">Saving…</span>
    </>
  );
}

function TrackButtonContent({
  state,
  position,
  title,
}: {
  state: BadgeState;
  position: string;
  title: string;
}) {
  if (state === 'submitting') return <TrackSavingRow position={position} />;
  return (
    <>
      <TrackPositionBadge position={position} state={state} />
      <span className="min-w-0 flex-1 truncate">{title}</span>
    </>
  );
}

/**
 * One row in the `TracklistPicker` list — a button that selects this
 * track on the parent release. Shows a "Saving…" state while a submit
 * is in flight, an amber highlight for the token-match autopilot's
 * pick, and a hover affordance otherwise.
 */
export function TrackPickButton({
  rel,
  track,
  submittingTrackKey,
  highlightedTrackPosition,
  onTrackPick,
}: {
  rel: SearchRelease;
  track: SearchTrack;
  submittingTrackKey: string | null;
  highlightedTrackPosition: string | null;
  onTrackPick: (rel: SearchRelease, t: SearchTrack) => void;
}) {
  const trackKey = buildTrackKey(rel.release_id, track.position);
  const isSubmitting = submittingTrackKey === trackKey;
  const state = resolveTrackButtonState(
    isSubmitting,
    isTrackHighlighted(highlightedTrackPosition, track.position),
  );
  const stateClass = resolveTrackBorderClass(state);

  return (
    <button
      type="button"
      disabled={submittingTrackKey !== null}
      onClick={(ev) => {
        ev.stopPropagation();
        onTrackPick(rel, track);
      }}
      style={{ touchAction: 'manipulation' }}
      className={`flex min-h-[64px] w-full cursor-pointer items-center gap-3 rounded-[10px] border px-4 py-3.5 text-left text-base transition-colors disabled:cursor-default disabled:opacity-60 ${stateClass}`}
    >
      <TrackButtonContent
        state={state}
        position={track.position ?? ''}
        title={(track.clean_title ?? track.title) ?? ''}
      />
    </button>
  );
}
