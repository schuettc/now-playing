import { pillContent } from './StatusPill';
import type { IdentifyState, Source } from '@/types';

export interface MatchInfo {
  /** Method label (e.g. `Shazam · matched`, `Fingerprint · remembered`). */
  method: string;
  /** Human-friendly relative time (e.g. `12s ago`, `just now`). */
  agoLabel: string;
}

/**
 * Picker uses the same content generator as the StatusPill so the
 * method label stays in sync with what the user sees in the pill.
 * Falls back to source label when the per-state sub is empty
 * (non-vinyl sources don't emit a method sub).
 */
function methodLabelFor(source: Source, identifyState: IdentifyState): string {
  const content = pillContent(source, identifyState);
  return content.sub || content.label;
}

/**
 * Renders a wall-clock delta as the shortest legible relative string.
 * Below 5s reads as "just now" so a freshly-confirmed match doesn't
 * race the user's eye. Above an hour we still emit hours; the picker
 * is unlikely to be open on tracks that old, but the label stays
 * sensible.
 */
export function formatRecognitionAgo(
  // Exported for testing; consumers should use `buildMatchInfo`.
  recognizedAtMs: number | null,
  nowMs: number,
): string | null {
  if (recognizedAtMs === null) return null;
  const deltaSec = Math.max(0, Math.floor((nowMs - recognizedAtMs) / 1000));
  if (deltaSec < 5) return 'just now';
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const min = Math.floor(deltaSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

export function buildMatchInfo(
  source: Source,
  identifyState: IdentifyState,
  recognizedAtMs: number | null,
  nowMs: number,
): MatchInfo {
  return {
    method: methodLabelFor(source, identifyState),
    agoLabel: formatRecognitionAgo(recognizedAtMs, nowMs) ?? '',
  };
}
