import type { RecentPlay } from '@/hooks/useRecentPlays';
import { LookupShell } from './shared';
import { SearchSection, useLookupSearch, useMountedAt } from './SearchSection';
import { ArtistChips } from './ArtistChips';

interface Props {
  /** Recents pulled by the orchestrator (single fetch) so the
      chip row reflects history even when this variant is the
      active layout for 0-4 recents. */
  recents: RecentPlay[] | null;
}

/**
 * Default variant — empty recents or initial load. Hero search at
 * the top, browse-by-artist chips below (sourced from recents),
 * collection grid (via SearchSection).
 *
 * Spec: docs/features/confirmed-fingerprint-coverage/design-output/
 * README.md § "Surface 3 — Lookup" → search-first.
 */
export function LookupViewSearchFirst({ recents }: Props) {
  const mountedAtMs = useMountedAt();
  const search = useLookupSearch();
  return (
    <LookupShell search={search} gap="md">
      <h1
        className="text-[44px] font-semibold tracking-tight"
        style={{ color: 'var(--text-primary)' }}
      >
        What's playing?
      </h1>
      <SearchSection
        variant="search-first"
        searchSize="lg"
        mountedAtMs={mountedAtMs}
        search={search}
      />
      <ArtistChips recents={recents} />
    </LookupShell>
  );
}
