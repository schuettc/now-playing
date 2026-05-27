import type { NowPlaying } from '@/types';

export type IdentifyScope = 'track' | 'album';

export interface SeedResult {
  seed: string;
  skipAutopilot: boolean;
  albumPickTrackTitle: string | null;
}

export function parseScope(search: string): IdentifyScope | null {
  const raw = new URLSearchParams(search).get('scope');
  return raw === 'track' || raw === 'album' ? raw : null;
}

/**
 * Read the `from=needs-id` flag. Set by the "Help identify this song"
 * link on the NEEDS_ID screen to mark the navigation as coming from a
 * "I don't know what's playing" intent. Downstream views use this to
 * suppress UI cues (current-track highlights, predicted-row markers)
 * that would otherwise prejudice the user toward the cascade's stale
 * guess. See docs/features/recents-one-tap-silent-pin/.
 */
export function parseFromNeedsId(search: string): boolean {
  return new URLSearchParams(search).get('from') === 'needs-id';
}

/**
 * Append `from=needs-id` to a path if the current search carries it.
 * Used by RecentsHero to preserve the intent flag across hops
 * (e.g. /lookup → /lookup?release=<id>).
 */
export function appendFromIfNeedsId(path: string, currentSearch: string): string {
  if (!parseFromNeedsId(currentSearch)) return path;
  const sep = path.includes('?') ? '&' : '?';
  return `${path}${sep}from=needs-id`;
}

export function computeSeed(
  scope: IdentifyScope,
  payload: NowPlaying,
): SeedResult {
  if (scope === 'track') {
    return {
      seed: payload.title || '',
      skipAutopilot: false,
      albumPickTrackTitle: null,
    };
  }
  const artist = (payload.artist || '').trim();
  const title = (payload.title || '').trim();
  const seed = [artist, title].filter(Boolean).join(' ');
  return {
    seed,
    skipAutopilot: true,
    albumPickTrackTitle: title || null,
  };
}

export async function fetchNowPlayingPayload(): Promise<NowPlaying | null> {
  try {
    const r = await fetch('/api/now-playing', { cache: 'no-store' });
    if (!r.ok) return null;
    const body = await r.json();
    return body?.payload ?? null;
  } catch {
    return null;
  }
}
