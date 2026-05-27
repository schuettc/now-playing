/**
 * Telemetry shim for the confirm-first UX A/B substrate.
 *
 * v1: emits via `console.log("[telemetry]", event, dims)`. A future
 * feature swaps this for an HTTP POST or analytics-SDK call without
 * touching call sites.
 *
 * Event catalogue + dimensions documented in
 * `docs/features/identify-confirm-first-ux/plan.md` § "Telemetry events".
 */

export type TelemetryEvent =
  | 'identify_guess_shown'
  | 'identify_guess_confirm'
  | 'identify_guess_reject'
  | 'identify_guess_pick_another'
  | 'identify_guess_timeout'
  | 'identify_tracklist_tap'
  | 'identify_lookup_open'
  | 'identify_lookup_pick'
  | 'identify_lookup_recent_tap'
  | 'identify_lookup_dismiss'
  | 'identify_pin_4xx'
  | 'identify_pin_timeout';

export function track(
  event: TelemetryEvent,
  dims: Record<string, unknown> = {},
): void {
  // eslint-disable-next-line no-console
  console.log('[telemetry]', event, dims);
}
