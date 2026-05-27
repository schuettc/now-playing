import type { Candidate } from './types';

interface Ids {
  releaseId: number | undefined;
  artist: string | undefined;
  album: string | undefined;
}

export type PickBody =
  | { release_id: number; url: string; source: Candidate['source'] }
  | {
      artist: string | undefined;
      album: string | undefined;
      url: string;
      source: Candidate['source'];
    };

export function buildPickBody(c: Candidate, ids: Ids): PickBody {
  return ids.releaseId
    ? { release_id: ids.releaseId, url: c.url, source: c.source }
    : { artist: ids.artist, album: ids.album, url: c.url, source: c.source };
}

export function buildResetQuery(ids: Ids): string {
  return ids.releaseId
    ? `release_id=${ids.releaseId}`
    : `artist=${encodeURIComponent(ids.artist ?? '')}&album=${encodeURIComponent(ids.album ?? '')}`;
}

export interface PickResult {
  ok: boolean;
  overrideUrl?: string;
  error?: string;
  networkError?: boolean;
}

export async function postArtOverride(body: PickBody): Promise<PickResult> {
  try {
    const res = await fetch('/api/art-override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const errBody = (await res.json().catch(() => ({}))) as {
        reason?: string;
      };
      return {
        ok: false,
        error:
          errBody.reason ?? 'Could not save your choice. Try another image.',
      };
    }
    const okBody = (await res.json().catch(() => ({}))) as {
      override_url?: string;
    };
    return { ok: true, overrideUrl: okBody.override_url };
  } catch {
    return { ok: false, error: 'Network error while saving.', networkError: true };
  }
}

export async function deleteArtOverride(qs: string): Promise<PickResult> {
  const res = await fetch(`/api/art-override?${qs}`, { method: 'DELETE' });
  if (!res.ok) return { ok: false, error: 'Could not reset to default.' };
  return { ok: true };
}
