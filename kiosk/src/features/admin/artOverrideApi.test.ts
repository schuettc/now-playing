import { describe, expect, it } from 'vitest';
import { buildPickBody, buildResetQuery } from './artOverrideApi';
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
