import { describe, expect, it } from 'vitest';
import { artIdentityOf } from './art';
import type { NowPlaying } from '@/types';

const base: NowPlaying = { ts: 't', state: 'PLAYING', source: 'vinyl' };

describe('artIdentityOf', () => {
  it('does not treat a null release_id as a release', () => {
    // Shazam-only vinyl: no Discogs release, so two different albums must
    // not share one "release:null" identity.
    const a = artIdentityOf({ ...base, release_id: null, artist: 'A', album: 'One' });
    const b = artIdentityOf({ ...base, release_id: null, artist: 'B', album: 'Two' });
    expect(a).toBe('A|One');
    expect(b).not.toBe(a);
  });

  it('falls back to the timestamp when every name field is null', () => {
    expect(
      artIdentityOf({ ...base, release_id: null, artist: null, album: null, art_url: null }),
    ).toBe('t');
  });

  it('uses the release id when there is one, including 0', () => {
    expect(artIdentityOf({ ...base, release_id: 0 })).toBe('release:0');
  });
});
