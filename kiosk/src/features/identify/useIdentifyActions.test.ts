import { describe, expect, it } from 'vitest';
import { findTrackInRelease } from './useIdentifyActions';
import { resolveToggleExpanded } from './identifyActionHelpers';
import type { SearchResponse } from './types';

const data: SearchResponse = {
  items: [
    {
      release_id: 10,
      artist: 'A',
      title: 'Album A',
      tracks: [
        { position: 'A1', title: 'Saturday Saviour' },
        { position: 'A2', title: 'Stuck on You' },
      ],
    },
  ],
  groups: [
    {
      artist: 'B',
      releases: [
        {
          release_id: 20,
          artist: 'B',
          title: 'Album B',
          tracks: [
            { position: 'B1', title: 'Stuck' },
            { position: 'B2', title: 'Open Up' },
          ],
        },
      ],
    },
  ],
};

describe('findTrackInRelease', () => {
  it('returns the track on exact case-insensitive title match', () => {
    expect(findTrackInRelease(data, 20, 'stuck')).toMatchObject({
      position: 'B1',
    });
    expect(findTrackInRelease(data, 20, 'STUCK')).toMatchObject({
      position: 'B1',
    });
  });

  it('falls back to substring match when no exact title hit', () => {
    expect(findTrackInRelease(data, 10, 'saturday')).toMatchObject({
      position: 'A1',
    });
  });

  it('prefers exact match over substring match', () => {
    // Both 'Stuck' (B1, exact) and 'Stuck on You' (A2, substring) match
    // "stuck"; the function must return the exact-title release when
    // queried against that release.
    expect(findTrackInRelease(data, 20, 'stuck')).toMatchObject({
      position: 'B1',
    });
    expect(findTrackInRelease(data, 10, 'stuck')).toMatchObject({
      position: 'A2',
    });
  });

  it('returns null when the release is not in the results', () => {
    expect(findTrackInRelease(data, 999, 'stuck')).toBeNull();
  });

  it('returns null on empty title', () => {
    expect(findTrackInRelease(data, 20, '')).toBeNull();
    expect(findTrackInRelease(data, 20, '   ')).toBeNull();
  });

  it('returns null when searchResults is null', () => {
    expect(findTrackInRelease(null, 20, 'stuck')).toBeNull();
  });
});

describe('resolveToggleExpanded', () => {
  const base = {
    searchResults: data,
    albumPickTrackTitle: null,
    expandedReleaseId: null,
    isSubmitting: false,
  };

  it('submits when album-pick resolves to a track', () => {
    expect(
      resolveToggleExpanded({
        ...base,
        releaseId: 20,
        albumPickTrackTitle: 'stuck',
      }),
    ).toEqual({ kind: 'submit', releaseId: 20, position: 'B1' });
  });

  it('falls through to expand when album-pick title has no match', () => {
    expect(
      resolveToggleExpanded({
        ...base,
        releaseId: 20,
        albumPickTrackTitle: 'nothing here',
      }),
    ).toEqual({ kind: 'expand', releaseId: 20 });
  });

  it('skips the album-pick shortcut while a submit is in flight', () => {
    expect(
      resolveToggleExpanded({
        ...base,
        releaseId: 20,
        albumPickTrackTitle: 'stuck',
        isSubmitting: true,
      }),
    ).toEqual({ kind: 'expand', releaseId: 20 });
  });

  it('collapses when tapping the currently-expanded release', () => {
    expect(
      resolveToggleExpanded({ ...base, releaseId: 20, expandedReleaseId: 20 }),
    ).toEqual({ kind: 'collapse' });
  });

  it('expands when tapping a different release', () => {
    expect(
      resolveToggleExpanded({ ...base, releaseId: 20, expandedReleaseId: 10 }),
    ).toEqual({ kind: 'expand', releaseId: 20 });
  });
});
