import type { RecentPlay } from '@/hooks/useRecentPlays';
import { useSearchSeed } from './searchSeedContext';

interface Props {
  recents: RecentPlay[] | null;
}

/** Pure helper: extract distinct artists from recent plays, preserving order. */
export function distinctArtists(
  recents: ReadonlyArray<RecentPlay> | null,
  limit: number = 8,
): string[] {
  if (!recents) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of recents) {
    const a = (r.artist ?? '').trim();
    if (!a) continue;
    if (seen.has(a)) continue;
    seen.add(a);
    out.push(a);
    if (out.length >= limit) break;
  }
  return out;
}

/**
 * Browse-by-artist chips for `LookupViewSearchFirst`. Sourced from
 * the user's recent plays so the chips are personally relevant —
 * if `useRecentPlays` is empty (fresh kiosk), the chip row hides
 * itself entirely rather than rendering placeholder content.
 *
 * Tapping a chip seeds the search box with the artist name; the
 * existing search machinery takes over from there.
 *
 * Restores the artist-browse surface flagged as plan-drift in the
 * D-5 impl review (originally specified by the legacy
 * `identify-artist-first-browse` feature; design Surface 3 calls
 * for "browse-by-artist chips below the hero").
 */
export function ArtistChips({ recents }: Props) {
  const seedSearch = useSearchSeed();
  const artists = distinctArtists(recents, 8);
  if (artists.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <span
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--text-tertiary)' }}
      >
        Browse by artist
      </span>
      <div className="flex flex-wrap gap-2">
        {artists.map((a) => (
          <button
            key={a}
            type="button"
            data-testid="artist-chip"
            onClick={() => seedSearch(a)}
            className="rounded-full px-4 py-2 text-[15px] transition-colors"
            style={{
              background: 'var(--text-hairline)',
              color: 'var(--text-body)',
            }}
          >
            {a}
          </button>
        ))}
      </div>
    </div>
  );
}
