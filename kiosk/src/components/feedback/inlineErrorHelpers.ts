/**
 * Map a `/api/pin-track` 4xx `reason` (or `timeout` / `network`, or
 * `select-release-failed` from the SomethingWrongPicker's alternates
 * flow) to a friendly mono-caps caption for the `InlineError` strip.
 *
 * Spec: docs/features/identify-confirm-first-ux/plan.md
 * § "Inline-error UX" (4xx reason table).
 */
export function inlineErrorCaption(reason: string): string {
  switch (reason) {
    case 'bad-request':
      return '! couldn\'t pin (bad request)';
    case 'no-album-locked':
      return '! album lock changed — try again';
    case 'release-id-mismatch':
      return '! album changed — try again';
    case 'position-not-in-tracklist':
      return '! track not in this album';
    case 'select-release-failed':
      return '! couldn\'t switch album — try again';
    case 'timeout':
    case 'network':
      return '! couldn\'t reach the kiosk';
    default:
      return '! couldn\'t pin';
  }
}
