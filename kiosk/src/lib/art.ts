import type { NowPlaying } from '@/types';

function releaseIdentity(d: NowPlaying): string | null {
  return d.release_id !== undefined ? `release:${d.release_id}` : null;
}

function hasNameData(d: NowPlaying): boolean {
  return Boolean(d.artist) || Boolean(d.album);
}

function nameIdentity(d: NowPlaying): string | null {
  if (!hasNameData(d)) return null;
  return `${d.artist ?? ''}|${d.album ?? ''}`;
}

/**
 * Stable identity for "what album is being rendered." Two consecutive
 * tracks from the same album should share an identity so AlbumArt stays
 * mounted instead of remounting on slightly different per-track URLs
 * (e.g. Sonos `getaa?vli=&u=…`).
 *
 * Precedence: release_id → artist|album → art_url → ts.
 */
export function artIdentityOf(d: NowPlaying): string {
  return releaseIdentity(d) ?? nameIdentity(d) ?? d.art_url ?? d.ts;
}

/**
 * Stable identity for "what track is being rendered." Distinct from
 * artIdentityOf so a track change within the same album animates the
 * text but not the artwork.
 *
 * Title-only is the disambiguator (NOT track_position): Discogs
 * enrichment can drop track_position across a pause/resume on streaming
 * sources, which would cause TrackInfo to exit-and-re-enter on every
 * pause (visible as a vertical jump). Title is the user's mental model
 * of "the song"; if it doesn't change, it's the same track.
 */
export function trackIdentityOf(d: NowPlaying): string {
  return `${artIdentityOf(d)}#${d.title ?? d.ts}`;
}

/** Replace an existing `v=` cache param in-place. */
function replaceCacheBust(url: string, bust: number): string {
  return url.replace(/([?&])v=[^&]*/, `$1v=${bust}`);
}

/** Append a fresh `v=` cache param, choosing `?` or `&` as the separator. */
function appendCacheBust(url: string, bust: number): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}v=${bust}`;
}

/**
 * Append or replace a `v=<bust>` cache-busting parameter on a URL. Used
 * after ArtPicker saves a new override so the visible <img> refetches
 * even though the URL is otherwise the same (the override path is
 * keyed by release_id or artist+album).
 *
 * POST /api/art-override returns an override URL with `v=<epoch>`
 * already set. Naively appending another `v=` produces `...&v=A&v=B`,
 * which is valid HTTP but ugly and ambiguous. Replace in place when a
 * `v` param is already there.
 */
export function withCacheBust(
  url: string | undefined,
  bust: number,
): string | undefined {
  if (!url || !bust) return url;
  if (/[?&]v=[^&]*/.test(url)) return replaceCacheBust(url, bust);
  return appendCacheBust(url, bust);
}
