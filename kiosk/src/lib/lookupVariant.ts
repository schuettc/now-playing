/**
 * Variant selection for the LookupView (Surface 3 of the
 * confirm-first design). Picked by recent-play data availability.
 *
 * Default during loading: `search-first` — clean first paint
 * regardless of how slow `/api/history/recent` is.
 *
 * Spec: docs/features/confirmed-fingerprint-coverage/design-output/
 * README.md § "Surface 3 — Lookup".
 */
export type LookupVariant = 'search-first' | 'recents-first' | 'hybrid';

export function pickLookupVariant(
  recents: ReadonlyArray<unknown> | null,
): LookupVariant {
  if (recents === null) return 'search-first';
  if (recents.length === 0) return 'search-first';
  if (recents.length >= 5) return 'recents-first';
  return 'hybrid';
}
