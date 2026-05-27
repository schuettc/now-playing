import { useEffect, useRef, type MutableRefObject } from 'react';
import type { NowPlaying } from '@/types';

/** Fire-and-forget prefetch of `/art/<rid>` — deduped by release id. */
function prefetchByReleaseId(
  rid: number | undefined | null,
  lastRef: MutableRefObject<number | string | null>,
): void {
  if (rid === undefined || rid === null) return;
  if (lastRef.current === rid) return;
  lastRef.current = rid;
  const img = new Image();
  img.src = `/art/${rid}`;
}

/** Fire-and-forget prefetch of an art-by-name URL — deduped by URL. */
function prefetchByArtUrl(
  url: string | undefined,
  lastRef: MutableRefObject<string | null>,
): void {
  if (!url || !url.startsWith('/art-by-name')) return;
  if (lastRef.current === url) return;
  lastRef.current = url;
  const img = new Image();
  img.src = url;
}

/**
 * Pre-warm the HTTP cache for the visible album art before the
 * `<AlbumArt>` component mounts. Two effects, two ref-keyed dedupes:
 *
 *  - `/art/<release_id>` — vinyl + Discogs-enriched tracks. Keyed by
 *    release_id (tracks within an album share art_url, so the only
 *    real jank is the first track of a new album).
 *  - `/art-by-name?…` — streaming/AirPlay with a saved override.
 *    Keyed by the URL itself (it embeds artist+album, so a
 *    track-internal change doesn't re-trigger).
 *
 * No externally visible state — fire-and-forget Image() prefetch.
 */
export function useArtPrefetch(data: NowPlaying | null): void {
  const lastPrefetchedRelease = useRef<number | string | null>(null);
  useEffect(() => {
    prefetchByReleaseId(data?.release_id, lastPrefetchedRelease);
  }, [data?.release_id]);

  const lastPrefetchedByName = useRef<string | null>(null);
  useEffect(() => {
    prefetchByArtUrl(data?.art_url, lastPrefetchedByName);
  }, [data?.art_url]);
}
