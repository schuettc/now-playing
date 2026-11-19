import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  artTargetOf,
  buildPickBody,
  buildResetQuery,
  deleteArtOverride,
} from './artOverrideApi';
import type { Candidate } from './types';

const candidate: Candidate = {
  url: 'https://x/y.jpg',
  source: 'caa',
  label: 'Cover',
};

describe('buildPickBody', () => {
  it('uses release_id when present', () => {
    expect(
      buildPickBody(candidate, {
        releaseId: 42,
        artist: 'A',
        album: 'B',
      }),
    ).toEqual({ release_id: 42, url: candidate.url, source: 'caa' });
  });

  it('falls back to artist+album when releaseId is missing', () => {
    expect(
      buildPickBody(candidate, {
        releaseId: undefined,
        artist: 'A',
        album: 'B',
      }),
    ).toEqual({ artist: 'A', album: 'B', url: candidate.url, source: 'caa' });
  });

  it('treats releaseId=0 as missing', () => {
    expect(
      buildPickBody(candidate, { releaseId: 0, artist: 'A', album: 'B' }),
    ).toEqual({ artist: 'A', album: 'B', url: candidate.url, source: 'caa' });
  });
});

describe('buildResetQuery', () => {
  it('uses release_id when present', () => {
    expect(
      buildResetQuery({ releaseId: 42, artist: 'A', album: 'B' }),
    ).toBe('release_id=42');
  });

  it('encodes artist and album when releaseId is missing', () => {
    expect(
      buildResetQuery({
        releaseId: undefined,
        artist: 'A & B',
        album: 'C/D',
      }),
    ).toBe('artist=A%20%26%20B&album=C%2FD');
  });

  it('coerces missing artist/album to empty string', () => {
    expect(
      buildResetQuery({
        releaseId: undefined,
        artist: undefined,
        album: undefined,
      }),
    ).toBe('artist=&album=');
  });
});

describe('artTargetOf', () => {
  it('captures the album the picker was opened for', () => {
    const opened = artTargetOf({
      ts: 't', state: 'PLAYING', source: 'airplay',
      artist: 'A', album: 'First', art_url: '/art-cache/a',
    });
    expect(opened).toEqual({
      releaseId: undefined, artist: 'A', album: 'First', currentArtUrl: '/art-cache/a',
    });
    expect(buildPickBody(candidate, opened)).toEqual({
      artist: 'A', album: 'First', url: candidate.url, source: 'caa',
    });
  });
});

describe('deleteArtOverride', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reports a network error instead of rejecting', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
    await expect(deleteArtOverride('release_id=1')).resolves.toEqual({
      ok: false,
      error: 'Network error while resetting.',
      networkError: true,
    });
  });
});
