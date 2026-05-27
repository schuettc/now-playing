import { describe, it, expect } from 'vitest';
import {
  appendFromIfNeedsId,
  computeSeed,
  parseFromNeedsId,
  parseScope,
} from './identifyScopeHelpers';
import type { NowPlaying } from '@/types';

const base: NowPlaying = {
  ts: '2026-01-01T00:00:00Z',
  state: 'PLAYING',
  source: 'vinyl',
};

describe('parseScope', () => {
  it('returns "track" for scope=track', () => {
    expect(parseScope('?scope=track')).toBe('track');
  });
  it('returns "album" for scope=album', () => {
    expect(parseScope('?scope=album')).toBe('album');
  });
  it('returns null when scope is missing', () => {
    expect(parseScope('')).toBeNull();
    expect(parseScope('?foo=bar')).toBeNull();
  });
  it('returns null for unknown scope values', () => {
    expect(parseScope('?scope=artist')).toBeNull();
    expect(parseScope('?scope=')).toBeNull();
  });
});

describe('computeSeed', () => {
  it('track scope seeds from title only, no autopilot skip', () => {
    expect(computeSeed('track', { ...base, title: 'Song', artist: 'A' })).toEqual({
      seed: 'Song',
      skipAutopilot: false,
      albumPickTrackTitle: null,
    });
  });

  it('track scope falls back to empty seed when title missing', () => {
    expect(computeSeed('track', { ...base, artist: 'A' })).toEqual({
      seed: '',
      skipAutopilot: false,
      albumPickTrackTitle: null,
    });
  });

  it('album scope joins artist and title and enables album-pick mode', () => {
    expect(
      computeSeed('album', { ...base, artist: 'Pink Floyd', title: 'Time' }),
    ).toEqual({
      seed: 'Pink Floyd Time',
      skipAutopilot: true,
      albumPickTrackTitle: 'Time',
    });
  });

  it('album scope with only artist yields artist seed and null trackTitle', () => {
    expect(computeSeed('album', { ...base, artist: 'Pink Floyd' })).toEqual({
      seed: 'Pink Floyd',
      skipAutopilot: true,
      albumPickTrackTitle: null,
    });
  });

  it('album scope with only title yields title seed and trackTitle', () => {
    expect(computeSeed('album', { ...base, title: 'Time' })).toEqual({
      seed: 'Time',
      skipAutopilot: true,
      albumPickTrackTitle: 'Time',
    });
  });

  it('album scope with empty fields yields empty seed', () => {
    expect(computeSeed('album', { ...base })).toEqual({
      seed: '',
      skipAutopilot: true,
      albumPickTrackTitle: null,
    });
  });

  it('album scope trims whitespace before joining', () => {
    expect(
      computeSeed('album', { ...base, artist: '  A  ', title: '  T  ' }),
    ).toEqual({
      seed: 'A T',
      skipAutopilot: true,
      albumPickTrackTitle: 'T',
    });
  });
});

describe('parseFromNeedsId', () => {
  it('returns true when from=needs-id is present', () => {
    expect(parseFromNeedsId('?from=needs-id')).toBe(true);
    expect(parseFromNeedsId('?release=123&from=needs-id')).toBe(true);
  });
  it('returns false when from is absent or different', () => {
    expect(parseFromNeedsId('')).toBe(false);
    expect(parseFromNeedsId('?from=')).toBe(false);
    expect(parseFromNeedsId('?from=elsewhere')).toBe(false);
    expect(parseFromNeedsId('?release=123')).toBe(false);
  });
});

describe('appendFromIfNeedsId', () => {
  it('passes the path through unchanged when flag absent', () => {
    expect(appendFromIfNeedsId('/lookup?release=1', '')).toBe('/lookup?release=1');
    expect(appendFromIfNeedsId('/lookup', '?other=x')).toBe('/lookup');
  });
  it('appends with ? when path has no query string', () => {
    expect(appendFromIfNeedsId('/lookup', '?from=needs-id')).toBe(
      '/lookup?from=needs-id',
    );
  });
  it('appends with & when path already has a query string', () => {
    expect(appendFromIfNeedsId('/lookup?release=42', '?from=needs-id')).toBe(
      '/lookup?release=42&from=needs-id',
    );
  });
});
