import { useEffect, useRef } from 'react';
import type { TracklistItem } from '@/types';
import { useStore } from '@/store/useStore';
import { useIdentifyContext } from '@/hooks/identifyContext';
import { TrackRow } from '@/components/TrackRow';
import { TappableTrackRow } from '@/components/tracklist/TappableTrackRow';
import { computeTracklistVisibility } from '@/components/tracklistVisibility';
import { shouldUseTappable } from '@/components/tracklist/tapState';
import { comparePosition } from '@/lib/positionSort';

interface RowArgs {
  track: TracklistItem;
  useTappable: boolean;
  releaseId: number | undefined;
  current: string | null | undefined;
  guessPos: string | null | undefined;
  peek: boolean;
}

function TappableRow(p: RowArgs) {
  return (
    <TappableTrackRow
      releaseId={p.releaseId as number}
      position={p.track.position}
      title={p.track.title}
      durationSeconds={p.track.duration_seconds}
      currentPosition={p.current ?? null}
      guessPosition={p.guessPos ?? null}
      peek={p.peek}
    />
  );
}

function PlainRow(p: RowArgs) {
  return (
    <TrackRow
      layoutId="tracklist-current-highlight"
      position={p.track.position}
      title={p.track.title}
      durationSeconds={p.track.duration_seconds}
      isCurrent={p.track.position === p.current}
      peek={p.peek}
    />
  );
}

function PanelRow(p: RowArgs) {
  const isTappable = p.useTappable && typeof p.releaseId === 'number';
  return isTappable ? <TappableRow {...p} /> : <PlainRow {...p} />;
}

// fallow-ignore-next-line complexity
function groupBySide(tracks: TracklistItem[]): Map<string, TracklistItem[]> {
  const groups = new Map<string, TracklistItem[]>();
  for (const t of tracks) {
    const side = t.side || (t.position ? t.position[0] : '') || '·';
    if (!groups.has(side)) groups.set(side, []);
    groups.get(side)!.push(t);
  }
  // Natural-sort within each side so B1..B11 render in numeric order
  // instead of the lexicographic B1, B10, B11, B2 the backend emits.
  for (const sideTracks of groups.values()) {
    sideTracks.sort((a, b) => comparePosition(a.position, b.position));
  }
  return groups;
}

/**
 * Scrolls the container so the currently-playing row sits ~1/3 from the
 * top of the visible viewport — clear of both the bottom fade gradient
 * (which used to mask the active row when it landed at the viewport
 * edge) and the top edge.
 *
 * Triggers only when the active track *identity* changes — the
 * (releaseId, position) tuple — not on every WS publish. Redundant
 * publishes that re-emit the same release+position are a no-op.
 *
 * The first scroll uses `behavior: 'instant'` to avoid a jarring slide
 * on app load (e.g. when the album is already mid-side). Subsequent
 * track changes use `'smooth'` to match the Framer Motion highlight-pill
 * transition feel.
 *
 * The 1/3-from-top placement is achieved by setting scroll-padding-top
 * on the container to ~33% of its height and asking scrollIntoView for
 * `block: 'start'`, so the browser handles the math.
 */
/**
 * Stable identity key for the currently-playing row. Returns null when
 * there's no active track. Used by useScrollToCurrent to detect actual
 * track-change events (vs WS publishes that re-emit the same row).
 */
export function currentTrackKey(
  releaseId: number | undefined,
  current: string | null | undefined,
): string | null {
  if (!current) return null;
  return `${releaseId ?? 'r'}::${current}`;
}

function useScrollToCurrent(
  releaseId: number | undefined,
  current: string | null | undefined,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isFirstScroll = useRef(true);
  const lastKey = useRef<string | null>(null);
  const key = currentTrackKey(releaseId, current);
  useEffect(() => {
    if (!key || key === lastKey.current) return;
    const container = containerRef.current;
    const el = container?.querySelector<HTMLElement>(
      '[data-is-current="true"]',
    );
    if (!container || !el) return;
    const behavior = isFirstScroll.current ? 'instant' : 'smooth';
    isFirstScroll.current = false;
    lastKey.current = key;
    // Why: scrollIntoView walks the scroll chain and can scroll ancestor
    // overflow-hidden containers (and the viewport itself), pushing
    // absolute-positioned top-right cluster items above the screen edge.
    // Compute scrollTop directly so only this container moves.
    const elRect = el.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const offsetWithinScroll =
      elRect.top - containerRect.top + container.scrollTop;
    const target = offsetWithinScroll - container.clientHeight / 3;
    container.scrollTo({ top: Math.max(0, target), behavior });
  }, [key]);
  return containerRef;
}

interface SideArgs {
  side: string;
  tracks: TracklistItem[];
  isPeekHeader: boolean;
  peekPositions: Set<string>;
  rowProps: Omit<RowArgs, 'track' | 'peek'>;
}

function SideSection({ side, tracks, isPeekHeader, peekPositions, rowProps }: SideArgs) {
  const headerToneClass = isPeekHeader ? 'text-white/30' : 'text-white/45';
  return (
    <div className="flex flex-col gap-1">
      <div
        className={`px-2 pb-1.5 pt-1 font-mono text-[13px] uppercase tracking-[0.32em] ${headerToneClass}`}
      >
        Side {side}
        {isPeekHeader && <span className="ml-3 text-white/25"> · flip next</span>}
      </div>
      {tracks.map((t) => (
        // data-is-current marks the active row for useScrollToCurrent's
        // querySelector. Wrapper div avoids threading a new prop into
        // TrackRow / TappableTrackRow. Uses rowProps.current to stay in
        // sync with the same source that drives the highlight pill.
        <div
          key={t.position}
          data-is-current={t.position === rowProps.current ? 'true' : undefined}
        >
          <PanelRow
            {...rowProps}
            track={t}
            peek={peekPositions.has(t.position)}
          />
        </div>
      ))}
    </div>
  );
}

// fallow-ignore-next-line complexity
function buildRowProps(
  payload: ReturnType<typeof useStore.getState>['payload'],
  pinPosition: string | null,
) {
  // pinPosition (from useIdentifyContext) supersedes the canonical
  // payload.track_position during the user-pinned optimistic window,
  // so the highlight pill moves to the tapped row before the WS
  // reconciliation arrives. impl-review-1 fix.
  const effectiveCurrent =
    pinPosition ?? (payload ? payload.track_position : undefined);
  return {
    useTappable: shouldUseTappable(payload),
    releaseId: payload ? payload.release_id : undefined,
    current: effectiveCurrent,
    guessPos: payload?.guess ? payload.guess.position : undefined,
  } satisfies Omit<RowArgs, 'track' | 'peek'>;
}

/**
 * Vinyl tracklist for the right column. When the locked source is
 * vinyl with a release_id, rows render via `TappableTrackRow`
 * (one-tap track pin per `identify-tappable-tracklist`). Otherwise
 * the plain informational `TrackRow` is used.
 *
 * The outer div is a scroll container (`overflow-y-auto h-full`) so the
 * panel stays within the shoulder-to-knee window defined by ShoulderColumn
 * and auto-scrolls to keep the active track visible on long sides.
 * scrollbar-hide: no Tailwind plugin in this project, so we hide via
 * inline style (Firefox: scrollbarWidth none) + Webkit CSS class.
 * scroll-padding-bottom: matches the 80px alpha-mask applied by
 * ShoulderColumn so scrollIntoView lands the row in the opaque zone.
 */
export function TracklistPanel() {
  const payload = useStore((s) => s.payload);
  const { pinPosition } = useIdentifyContext();
  const tracks = payload && payload.tracklist;

  const rowProps = buildRowProps(payload, pinPosition);
  const scrollRef = useScrollToCurrent(rowProps.releaseId, rowProps.current);

  if (!tracks || tracks.length === 0) return null;

  const { tracks: visible, peekPositions, peekHeaderSide } =
    computeTracklistVisibility(tracks, rowProps.current ?? '');
  const sides = Array.from(groupBySide(visible).entries());

  return (
    <div
      ref={scrollRef}
      data-testid="tracklist-panel"
      className="flex h-full w-full flex-col gap-4 overflow-y-auto text-left [&::-webkit-scrollbar]:hidden"
      style={{ scrollbarWidth: 'none', scrollPaddingBottom: '80px' }}
    >
      {sides.map(([side, sideTracks]) => (
        <SideSection
          key={side}
          side={side}
          tracks={sideTracks}
          isPeekHeader={side === peekHeaderSide}
          peekPositions={peekPositions}
          rowProps={rowProps}
        />
      ))}
    </div>
  );
}
