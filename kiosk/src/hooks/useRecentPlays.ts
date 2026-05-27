import { useEffect, useState } from 'react';

export interface RecentPlay {
  release_id: number | null;
  track_position: string | null;
  artist: string | null;
  title: string | null;
  album: string | null;
  match_method: string | null;
  source: string | null;
  /** Unix seconds (mapped from the endpoint's `started_at`). */
  ts: number;
  /** Derived as `/art/<release_id>` when `release_id` is set —
      hits the existing art-cache proxy. Backend has no `art_url`. */
  art_url: string | undefined;
}

interface ApiPlayRow {
  release_id: number | null;
  track_position: string | null;
  artist: string | null;
  title: string | null;
  album: string | null;
  match_method: string | null;
  source: string | null;
  started_at: number;
  ended_at: number;
}

export function rowToRecent(row: ApiPlayRow): RecentPlay {
  return {
    release_id: row.release_id,
    track_position: row.track_position,
    artist: row.artist,
    title: row.title,
    album: row.album,
    match_method: row.match_method,
    source: row.source,
    ts: row.started_at,
    art_url: row.release_id !== null ? `/art/${row.release_id}` : undefined,
  };
}

export interface UseRecentPlays {
  recents: RecentPlay[] | null;
  loading: boolean;
  error: Error | null;
}

/**
 * Fetch the most recent plays from `/api/history/recent`. Simple
 * useState/useEffect — refetches on `limit` change. The LookupView
 * is transient (open, pick, leave), so stale-while-revalidate isn't
 * necessary in v1.
 */
export function useRecentPlays(limit: number): UseRecentPlays {
  const [recents, setRecents] = useState<RecentPlay[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const resp = await fetch(`/api/history/recent?limit=${limit}`);
        if (!resp.ok) throw new Error(`status=${resp.status}`);
        const body = (await resp.json()) as { plays: ApiPlayRow[] };
        if (cancelled) return;
        setRecents(body.plays.map(rowToRecent));
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setRecents(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [limit]);

  return { recents, loading, error };
}
