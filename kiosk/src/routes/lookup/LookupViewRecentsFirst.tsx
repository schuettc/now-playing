import type { RecentPlay } from '@/hooks/useRecentPlays';
import { LookupShell } from './shared';
import { SearchSection, useLookupSearch, useMountedAt } from './SearchSection';
import { RecentsHero } from './RecentsHero';

interface Props {
  recents: RecentPlay[];
}

/**
 * `recents-first` variant — ≥5 recent plays available. 5 large
 * AlbumCard (lg) hero row at top, search section below.
 */
export function LookupViewRecentsFirst({ recents }: Props) {
  const mountedAtMs = useMountedAt();
  const search = useLookupSearch();
  return (
    <LookupShell search={search} gap="lg">
      <RecentsHero
        variant="recents-first"
        recents={recents}
        size="lg"
        limit={5}
        mountedAtMs={mountedAtMs}
      />
      <SearchSection
        variant="recents-first"
        mountedAtMs={mountedAtMs}
        search={search}
      />
    </LookupShell>
  );
}
