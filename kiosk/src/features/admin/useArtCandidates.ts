import { useEffect, useState } from 'react';
import type { Candidate } from './types';

interface Args {
  releaseId: number | undefined;
  artist: string | undefined;
  album: string | undefined;
  currentArtUrl: string | undefined;
  /**
   * When false the hook tears down any existing EventSource and clears
   * state. Used by the parent modal to gate the SSE connection on the
   * modal being open + having identifying data.
   */
  enabled: boolean;
}

/** Build the name-based query params including optional current_url. */
function buildByNameQS(
  artist: string | undefined,
  album: string | undefined,
  currentArtUrl: string | undefined,
): string {
  const base = `artist=${encodeURIComponent(artist ?? '')}&album=${encodeURIComponent(album ?? '')}`;
  const currentParam = currentArtUrl
    ? `&current_url=${encodeURIComponent(currentArtUrl)}`
    : '';
  return `${base}${currentParam}`;
}

/**
 * Build the query string for `/api/art-candidates`.
 *
 * When a release_id is known, use it directly. Otherwise fall back to
 * artist+album name, appending the kiosk's currently-rendered art_url
 * so the server's "Current" tile has something displayable on the
 * by-name path (streaming/AirPlay without a Discogs match).
 */
function buildArtCandidatesQS(
  releaseId: number | undefined,
  artist: string | undefined,
  album: string | undefined,
  currentArtUrl: string | undefined,
): string {
  if (releaseId !== undefined) return `release_id=${releaseId}`;
  return buildByNameQS(artist, album, currentArtUrl);
}

/**
 * Streams album-art candidates from `/api/art-candidates` via Server-
 * Sent Events. Returns the accumulated list + a `streamDone` flag
 * (true after `done` or `error`). De-dupes by URL on push.
 *
 * Sources include Discogs masters/releases, MusicBrainz CAA, and the
 * caller's "current" art (for the by-name path where the server can't
 * derive a fallback).
 */
export function useArtCandidates({
  releaseId,
  artist,
  album,
  currentArtUrl,
  enabled,
}: Args): { candidates: Candidate[]; streamDone: boolean } {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [streamDone, setStreamDone] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setCandidates([]);
      setStreamDone(false);
      return;
    }
    const qs = buildArtCandidatesQS(releaseId, artist, album, currentArtUrl);
    const es = new EventSource(`/api/art-candidates?${qs}`);
    es.onmessage = (ev) => {
      try {
        const c = JSON.parse(ev.data) as Candidate;
        if (!c.url) return;
        setCandidates((prev) =>
          prev.some((p) => p.url === c.url) ? prev : [...prev, c],
        );
      } catch {
        // Malformed SSE frame — skip.
      }
    };
    es.addEventListener('done', () => {
      setStreamDone(true);
      es.close();
    });
    es.onerror = () => {
      setStreamDone(true);
      es.close();
    };
    return () => es.close();
  }, [enabled, releaseId, artist, album, currentArtUrl]);

  return { candidates, streamDone };
}
