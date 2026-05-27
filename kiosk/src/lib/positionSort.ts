/**
 * Natural-sort comparator for vinyl track position strings.
 *
 * Track positions arrive as strings like `A1`, `B10`, `B2`, `A1a`, `B1.b`.
 * A naive lexicographic sort puts `B10` before `B2` because `'1' < '2'`.
 * This comparator splits a position into side-prefix + integer + suffix
 * and compares the integer numerically so:
 *
 *   B1 < B2 < B9 < B10 < B11
 *   A1 < A1a < A1b < A2          (subtracks adjacent to their parent)
 *   A1 < B1 < C1 < D1            (side ordering)
 *
 * Patterns the regex understands:
 *   - `A1`, `B10`                — side + integer
 *   - `A1a`, `B1.b`              — side + integer + subtrack letter
 *   - `D15`                      — multi-LP cumulative positions
 *
 * Anything that doesn't match falls back to `localeCompare` so unknown
 * label shapes (e.g. `CD1-3`) still produce a stable order.
 */
const POSITION_RE = /^([A-Z]+)(\d+)\.?([a-z]?)$/i;

export function comparePosition(a: string, b: string): number {
  const ma = a.match(POSITION_RE);
  const mb = b.match(POSITION_RE);
  if (!ma || !mb) return a.localeCompare(b);
  const sideCmp = ma[1].toUpperCase().localeCompare(mb[1].toUpperCase());
  if (sideCmp !== 0) return sideCmp;
  const numCmp = parseInt(ma[2], 10) - parseInt(mb[2], 10);
  if (numCmp !== 0) return numCmp;
  return ma[3].toLowerCase().localeCompare(mb[3].toLowerCase());
}
