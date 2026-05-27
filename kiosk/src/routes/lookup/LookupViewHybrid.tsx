import type { RecentPlay } from '@/hooks/useRecentPlays';
import { LookupShell } from './shared';
import { SearchSection, useLookupSearch, useMountedAt } from './SearchSection';
import { RecentsHero } from './RecentsHero';

interface Props {
  recents: RecentPlay[];
}

/**
 * `hybrid` variant — 1-4 recent plays. Medium AlbumCard (md) row
 * + search section.
 */
export function LookupViewHybrid({ recents }: Props) {
  const mountedAtMs = useMountedAt();
  const search = useLookupSearch();
  return (
    <LookupShell search={search} gap="md">
      <RecentsHero
        variant="hybrid"
        recents={recents}
        size="md"
        limit={6}
        mountedAtMs={mountedAtMs}
      />
      <SearchSection
        variant="hybrid"
        mountedAtMs={mountedAtMs}
        search={search}
      />
    </LookupShell>
  );
}
