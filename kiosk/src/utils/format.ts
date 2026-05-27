/**
 * Formatting helpers shared across kiosk components.
 */

/**
 * Format a duration in seconds as M:SS. Returns '' for missing or
 * non-positive values so the caller can render an empty cell without
 * a special case (Sonos streaming queue items, for example, don't
 * carry duration without a per-item SOAP call).
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '';
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/**
 * Bucket table for `formatRelativeTime`. Each row is
 * `[thresholdSec, divisorSec, unit]`: if `diff < thresholdSec`, render
 * `Math.round(diff / divisorSec) <unit>(s) ago`. Buckets are checked
 * in order; the final row (years) has `threshold = Infinity` so it
 * always matches anything that fell past every earlier bucket.
 *
 * Thresholds match the original if-ladder exactly: 60s, 1h, 1d, 14d,
 * 60d, 2y. The order matters — small buckets first.
 */
const RELATIVE_TIME_BUCKETS: ReadonlyArray<
  readonly [thresholdSec: number, divisorSec: number, unit: string]
> = [
  [3600, 60, 'minute'],
  [86400, 3600, 'hour'],
  [86400 * 14, 86400, 'day'],
  [86400 * 60, 86400 * 7, 'week'],
  [86400 * 365 * 2, 86400 * 30, 'month'],
  [Infinity, 86400 * 365, 'year'],
];

/**
 * Format a unix-epoch-seconds timestamp as a coarse "N minutes/hours/days
 * ago" string. Buckets: just now (<60s), minutes (<1h), hours (<1d),
 * days (<14d), weeks (<60d), months (<2y), then years. Future timestamps
 * (negative diff) are clamped to "just now".
 */
export function formatRelativeTime(epochSec: number): string {
  const now = Math.floor(Date.now() / 1000);
  const diff = Math.max(0, now - epochSec);
  if (diff < 60) return 'just now';
  for (const [threshold, divisor, unit] of RELATIVE_TIME_BUCKETS) {
    if (diff < threshold) {
      const value = Math.round(diff / divisor);
      return `${value} ${unit}${value === 1 ? '' : 's'} ago`;
    }
  }
  // Unreachable: the last bucket's threshold is Infinity.
  return 'just now';
}
