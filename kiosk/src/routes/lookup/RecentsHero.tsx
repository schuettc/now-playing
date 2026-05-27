import { useLocation, useSearch } from 'wouter';
import { AlbumCard } from '@/components/touch/AlbumCard';
import { track as telemetryTrack } from '@/lib/telemetry';
import type { RecentPlay } from '@/hooks/useRecentPlays';
import type { LookupVariant } from '@/lib/lookupVariant';
import { appendFromIfNeedsId } from '@/features/identify/identifyScopeHelpers';
import { useSearchSeed } from './searchSeedContext';

interface Props {
  variant: LookupVariant;
  recents: RecentPlay[];
  size: 'md' | 'lg';
  /** Maximum cards to render. Default 5 (matches design spec). */
  limit?: number;
  mountedAtMs: number;
}

/**
 * Hero row of recent plays for the `recents-first` and `hybrid`
 * variants.
 *
 * Tap behaviour (D-5 one-tap recents):
 * - Cards with a `release_id` navigate to `/lookup?release=<id>`,
 *   landing the user directly on the scoped tracklist. Tap a track
 *   → identify → bounce back to `/`.
 * - Cards without a `release_id` (legacy plays pre-dating the column)
 *   fall back to seeding the search box so the user can still find
 *   the album.
 *
 * `useSearchSeed` is called unconditionally (Rules of Hooks). The
 * branch on `release_id` happens at runtime inside `handleTap`.
 */
export function RecentsHero({
  variant, recents, size, limit = 5, mountedAtMs,
}: Props) {
  const [, navigate] = useLocation();
  const currentSearch = useSearch();
  const seedSearch = useSearchSeed();
  const cards = recents.slice(0, limit);

  if (cards.length === 0) return null;

  const handleTap = (r: RecentPlay) => {
    const outcome = r.release_id !== null ? 'tracklist' : 'search';
    telemetryTrack('identify_lookup_recent_tap', {
      variant,
      ms_to_tap: Date.now() - mountedAtMs,
      outcome,
    });
    if (r.release_id !== null) {
      // Navigate to the scoped tracklist for this specific past album.
      // Preserve the from=needs-id flag so the scoped view knows to
      // suppress current/guess highlights that would bias the pick.
      navigate(appendFromIfNeedsId(`/lookup?release=${r.release_id}`, `?${currentSearch}`));
    } else {
      // Legacy fallback: seed the search box when release_id is absent.
      const seed = [r.artist, r.album].filter(Boolean).join(' ');
      if (seed) seedSearch(seed);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h2
        className="text-[20px] font-semibold"
        style={{ color: 'var(--text-body)' }}
      >
        {variant === 'recents-first' ? 'Did you just play one of these?' : 'Recent plays'}
      </h2>
      <div className="flex flex-wrap gap-6">
        {cards.map((r, i) => (
          <AlbumCard
            key={`${r.release_id}-${r.ts}-${i}`}
            art={r.art_url}
            title={r.album ?? r.title ?? '—'}
            subtitle={r.artist ?? undefined}
            size={size}
            onClick={() => handleTap(r)}
          />
        ))}
      </div>
    </div>
  );
}
