import type { TracklistItem } from '@/types';

export interface TracklistVisibility {
  /** The tracks the kiosk should render (current side, optionally + next-side peek row). */
  tracks: TracklistItem[];
  /** Positions in `tracks` that should be styled as peek rows (dimmed). */
  peekPositions: Set<string>;
  /**
   * If a peek row was appended, the side it belongs to — so the panel
   * can render a dimmed "Side X · flip next" subheader above it. Null
   * when no peek.
   */
  peekHeaderSide: string | null;
}

/**
 * Filter the full tracklist down to what the kiosk shows at a distance.
 *
 * Rules (see docs/features/kiosk-distance-readability/idea.md):
 *   1. Default: current side only.
 *   2. End-of-side peek: when the current track is the last entry on
 *      its side AND a later side exists in original tracklist order,
 *      append that side's first track and mark it as peek.
 *   3. End-of-record: no peek.
 *   4. Side-source fallback chain:
 *        a. current track's `side` field
 *        b. current position's first char (e.g. 'A' from 'A1')
 *        c. only if both yield empty → return all tracks (matches
 *           today's behavior; covers pre-lock + sideless compilations)
 */
export function computeTracklistVisibility(
  tracks: TracklistItem[],
  currentPosition: string | null | undefined,
): TracklistVisibility {
  if (tracks.length === 0 || !currentPosition) {
    return { tracks, peekPositions: new Set(), peekHeaderSide: null };
  }

  const currentTrack = tracks.find((t) => t.position === currentPosition);

  // Side-source fallback chain: track's side field → position[:1] → bail.
  // Using position[:1] keeps the current-side view working for records
  // whose Discogs metadata omitted the per-track side field but whose
  // position strings still encode the side (the common case).
  const sideFromTrack = currentTrack?.side ?? null;
  const sideFromPosition = currentPosition[0];
  const currentSide = sideFromTrack || sideFromPosition;

  if (!currentSide) {
    return { tracks, peekPositions: new Set(), peekHeaderSide: null };
  }

  const matchesSide = (t: TracklistItem): boolean => {
    // When we derived side from position[:1], the track rows may
    // similarly not have an explicit `side` field; match on either
    // their side or the first char of their position.
    const trackSide = t.side ?? (t.position ? t.position[0] : null);
    return trackSide === currentSide;
  };

  const sideTracks = tracks.filter(matchesSide);
  if (sideTracks.length === 0) {
    // Side derivation produced something but no rows match — fall back
    // to today's all-tracks render so the panel never renders empty.
    return { tracks, peekPositions: new Set(), peekHeaderSide: null };
  }

  const isLastOfSide =
    sideTracks[sideTracks.length - 1].position === currentPosition;
  if (!isLastOfSide) {
    return { tracks: sideTracks, peekPositions: new Set(), peekHeaderSide: null };
  }

  // Find first track of the next side: the first track in the original
  // tracklist (after the last track of the current side) whose effective
  // side differs from currentSide. "Next side" = "first unique side
  // value AFTER the current side in tracklist order" — NOT an
  // alphabetic increment. Handles A→B→C, 1→2→3, and arbitrary labels.
  const lastIdxOfCurrentSide = tracks.findIndex(
    (t) => t.position === sideTracks[sideTracks.length - 1].position,
  );
  const peekTrack = tracks.slice(lastIdxOfCurrentSide + 1).find((t) => {
    const trackSide = t.side ?? (t.position ? t.position[0] : null);
    return trackSide && trackSide !== currentSide;
  });

  if (!peekTrack) {
    return { tracks: sideTracks, peekPositions: new Set(), peekHeaderSide: null };
  }

  const peekSide = peekTrack.side ?? (peekTrack.position ? peekTrack.position[0] : null);
  return {
    tracks: [...sideTracks, peekTrack],
    peekPositions: new Set([peekTrack.position]),
    peekHeaderSide: peekSide,
  };
}
