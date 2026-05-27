import { useEffect } from 'react';
import type { NowPlaying } from '@/types';
import { useStore, albumContextKey } from '@/store/useStore';
import { buildCornerLinks } from './cornerLinks.helpers';

interface Props {
  data: NowPlaying | null;
  showTrack: boolean;
  showNeedsId: boolean;
  showVinylIdentifying: boolean;
}

function DiscogsLink({ releaseId }: { releaseId: number }) {
  return (
    <div className="absolute bottom-4 right-4 z-20 text-xs tracking-wide text-white/30">
      <a
        href={`https://www.discogs.com/release/${releaseId}`}
        target="_blank"
        rel="noopener noreferrer"
        className="transition-colors hover:text-white/60"
      >
        View on Discogs ↗
      </a>
    </div>
  );
}

function WikipediaLink() {
  const release_id = useStore((s) => s.payload?.release_id);
  const artist = useStore((s) => s.payload?.artist);
  const album = useStore((s) => s.payload?.album);
  const key = albumContextKey({ releaseId: release_id, artist, album });
  const ctx = useStore((s) => (key ? s.albumContext.get(key) ?? null : null));
  const fetchAlbumContext = useStore((s) => s.fetchAlbumContext);

  // Trigger the wiki-context fetch when the album changes. This logic
  // migrated from ContextPanel.tsx (deleted in Task 8) — WikipediaLink
  // is now the sole owner of the fetch since the inline panel is gone.
  useEffect(() => {
    if (!key) return;
    fetchAlbumContext({ releaseId: release_id, artist, album });
  }, [key, release_id, artist, album, fetchAlbumContext]);

  if (!key || !ctx || !ctx.url) return null;

  return (
    <div className="absolute bottom-4 left-10 z-20 text-xs tracking-wide text-white/30">
      <a
        href={ctx.url}
        target="_blank"
        rel="noopener noreferrer"
        className="transition-colors hover:text-white/60"
      >
        Read on Wikipedia ↗
      </a>
    </div>
  );
}

/**
 * Bottom-edge link cluster:
 *   - Lower-LEFT: "Read on Wikipedia ↗" when a wiki URL is resolved
 *     for the current track's album (replaces the inline
 *     ContextPanel that used to live in the left column).
 *   - Lower-RIGHT: "View on Discogs ↗" when a release_id is locked.
 *
 * The previous centered "Wrong track? / Wrong album?" cluster was
 * retired by `identify-learning-chip-undo-strip` — `UndoStrip` is
 * now the single "wrong track" affordance, anchored under the
 * StatusPill at top-right.
 */
export function CornerLinks(props: Props) {
  const view = buildCornerLinks(props);

  return (
    <>
      {props.showTrack && <WikipediaLink />}
      {view.discogsReleaseId !== null && (
        <DiscogsLink releaseId={view.discogsReleaseId} />
      )}
    </>
  );
}
