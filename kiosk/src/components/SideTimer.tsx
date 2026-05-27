import { useMemo } from 'react';
import { motion } from 'framer-motion';
import type { NowPlaying, TracklistItem } from '@/types';

interface Props {
  data: NowPlaying;
}

interface SideSummary {
  currentSide: string;
  sideTrackCount: number;
  currentSideIndex: number;
}

function sideOf(item: TracklistItem): string {
  return (item.side || (item.position?.[0] ?? '')).toUpperCase();
}

/** Derive the vinyl side letter from optional side field or first char of track position. */
function resolveSideLetter(side: string | undefined, track_position: string): string {
  return (side ?? track_position[0] ?? '').toUpperCase();
}

/** Build the 1-based side-track index for the current position. */
function buildSideIndex(sideTracks: TracklistItem[], track_position: string): number {
  return sideTracks.findIndex((t) => t.position === track_position) + 1;
}

/** Build the summary object for a known-valid tracklist + position. */
function buildSummary(
  tracklist: TracklistItem[],
  track_position: string,
  side: string | undefined,
): SideSummary | null {
  if (!tracklist.some((t) => t.position === track_position)) return null;
  const currentSide = resolveSideLetter(side, track_position);
  const sideTracks = tracklist.filter((t) => sideOf(t) === currentSide);
  return {
    currentSide,
    sideTrackCount: sideTracks.length,
    currentSideIndex: buildSideIndex(sideTracks, track_position),
  };
}

/**
 * Compute which side and track-index we're on given the full tracklist.
 * Returns null when the track position isn't found or data is missing.
 */
function computeSideSummary(
  tracklist: TracklistItem[] | undefined,
  track_position: string | undefined,
  side: string | undefined,
): SideSummary | null {
  if (!tracklist || tracklist.length === 0 || !track_position) return null;
  return buildSummary(tracklist, track_position, side);
}

/**
 * Side index badge — "Side B · 4/5". Pure render from the published
 * tracklist; no live timer.
 *
 * Earlier versions also computed elapsed/remaining track time for a
 * "Track MM:SS" line. That line was removed (see
 * docs/features/remove-track-time-display/) and the underlying
 * remaining-time calculation was kept around with a comment claiming
 * it fed `anticipated-track-end`. It didn't — `anticipated-track-end`
 * lives entirely in the orchestrator (`pi/nowplaying/main.py`
 * `_maybe_arm_anticipation`). The remaining-time math is gone as of
 * 2026-05-14's audit pass.
 */
export function SideTimer({ data }: Props) {
  const { tracklist, track_position } = data;

  const summary = useMemo(
    () => computeSideSummary(tracklist, track_position, data.side),
    [tracklist, track_position, data.side],
  );

  if (!summary) return null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6, delay: 0.3 }}
      className="flex flex-col items-end gap-1 font-mono text-[11px] uppercase tracking-[0.3em] text-white/55"
    >
      <div>
        Side {summary.currentSide} · {summary.currentSideIndex}/{summary.sideTrackCount}
      </div>
    </motion.div>
  );
}
