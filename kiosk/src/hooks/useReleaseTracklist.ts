import { useEffect, useState } from 'react';
import type { TracklistItem } from '@/types';

interface ApiTrack {
  position: string;
  side: string | null;
  title: string;
  clean_title?: string | null;
  duration_seconds: number | null;
}

function toTracklistItem(t: ApiTrack): TracklistItem {
  return {
    position: t.position,
    side: t.side ?? null,
    title: t.clean_title ?? t.title,
    duration_seconds: t.duration_seconds ?? 0,
  };
}

async function fetchTracks(releaseId: number, signal: AbortSignal): Promise<TracklistItem[]> {
  const resp = await fetch(`/api/release/${releaseId}/tracklist`, { signal });
  if (!resp.ok) {
    // 404 = release not in catalog; 5xx = server error.
    // Either way, surface as empty so the UI can show a fallback.
    return [];
  }
  const body = (await resp.json()) as { ok: boolean; tracks: ApiTrack[] };
  return body.tracks.map(toTracklistItem);
}

/**
 * Fetch the ordered tracklist for a specific release from
 * `GET /api/release/<id>/tracklist`. Used when the scoped lookup
 * path needs tracks for a *past* album that is no longer the
 * currently-playing payload (which only carries the locked album's
 * tracklist).
 *
 * Returns:
 *   - `tracks: null`  — fetch in-flight (loading)
 *   - `tracks: []`    — release not in local catalog (404), has no tracks,
 *                       or a network/5xx error occurred
 *   - `tracks: [...]` — populated tracklist
 *
 * Cancels the in-flight request if `releaseId` changes or the
 * component unmounts.
 */
export function useReleaseTracklist(releaseId: number | null): {
  tracks: TracklistItem[] | null;
  loading: boolean;
} {
  const [tracks, setTracks] = useState<TracklistItem[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (releaseId === null) {
      setTracks(null);
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setTracks(null);

    fetchTracks(releaseId, controller.signal).then(
      (result) => {
        setTracks(result);
        setLoading(false);
      },
      (err: unknown) => {
        const isAbort = err instanceof DOMException && err.name === 'AbortError';
        if (!isAbort) {
          // Network error — show empty tracklist
          setTracks([]);
          setLoading(false);
        }
      },
    );

    return () => { controller.abort(); };
  }, [releaseId]);

  return { tracks, loading };
}
