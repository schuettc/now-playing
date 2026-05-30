import { describe, expect, it } from 'vitest';
import {
  applyTokenMatch,
  findTrackMatch,
  hasArtistOrAlbumMatch,
} from './useIdentifySearch';
import type { SearchResponse, SearchTrack } from './types';

const rel = (
  release_id: number,
  artist: string,
  title: string,
  tracks: Array<{ position: string; title: string }> = [],
) => ({ release_id, artist, title, tracks });

describe('applyTokenMatch', () => {
  it('returns nulls for empty or whitespace tokens', () => {
    const data: SearchResponse = {
      items: [rel(1, 'Failure', 'Fantastic Planet')],
      groups: [],
    };
    expect(applyTokenMatch(data, '')).toEqual({
      releaseId: null,
      position: null,
    });
    expect(applyTokenMatch(data, '   ')).toEqual({
      releaseId: null,
      position: null,
    });
  });

  it('does not auto-expand when the token matches an artist', () => {
    // Typing "failure" should leave Failure-the-artist results visible;
    // it shouldn't auto-jump into ATUM just because some track is
    // titled "Failure".
    const data: SearchResponse = {
      items: [],
      groups: [
        {
          artist: 'Failure',
          releases: [
            rel(1, 'Failure', 'Fantastic Planet', [
              { position: 'A1', title: 'Saturday Saviour' },
            ]),
            rel(2, 'Smashing Pumpkins', 'ATUM', [
              { position: 'A1', title: 'Failure' },
            ]),
          ],
        },
      ],
    };
    expect(applyTokenMatch(data, 'failure')).toEqual({
      releaseId: null,
      position: null,
    });
  });

  it('does not auto-expand when the token matches an album title', () => {
    const data: SearchResponse = {
      items: [rel(1, 'Failure', 'Fantastic Planet')],
      groups: [],
    };
    expect(applyTokenMatch(data, 'fantastic')).toEqual({
      releaseId: null,
      position: null,
    });
  });

  it('expands the release whose track title matches the token', () => {
    const data: SearchResponse = {
      items: [
        rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Saturday Saviour' }]),
        rel(2, 'B', 'Album B', [{ position: 'B2', title: 'Stuck' }]),
      ],
      groups: [],
    };
    expect(applyTokenMatch(data, 'stuck')).toEqual({
      releaseId: 2,
      position: 'B2',
    });
  });

  it('handles leading/trailing whitespace and mixed case', () => {
    const data: SearchResponse = {
      items: [rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Stuck' }])],
      groups: [],
    };
    expect(applyTokenMatch(data, '  STUCK  ')).toEqual({
      releaseId: 1,
      position: 'A1',
    });
  });

  it('returns nulls when nothing matches', () => {
    const data: SearchResponse = {
      items: [rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Hello' }])],
      groups: [],
    };
    expect(applyTokenMatch(data, 'xyzzy')).toEqual({
      releaseId: null,
      position: null,
    });
  });
});

describe('hasArtistOrAlbumMatch', () => {
  it('matches a group artist case-insensitively', () => {
    const data: SearchResponse = {
      items: [],
      groups: [{ artist: 'Failure', releases: [rel(1, 'Failure', 'X')] }],
    };
    expect(hasArtistOrAlbumMatch(data, 'failure')).toBe(true);
  });

  it('matches a release artist on ungrouped items', () => {
    const data: SearchResponse = {
      items: [rel(1, 'Smashing Pumpkins', 'ATUM')],
      groups: [],
    };
    expect(hasArtistOrAlbumMatch(data, 'smashing')).toBe(true);
  });

  it('matches a release title (album)', () => {
    const data: SearchResponse = {
      items: [rel(1, 'A', 'Fantastic Planet')],
      groups: [],
    };
    expect(hasArtistOrAlbumMatch(data, 'fantastic')).toBe(true);
  });

  it('returns false when only a track title matches', () => {
    const data: SearchResponse = {
      items: [rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Stuck' }])],
      groups: [],
    };
    expect(hasArtistOrAlbumMatch(data, 'stuck')).toBe(false);
  });

  it('returns false for empty needles', () => {
    const data: SearchResponse = {
      items: [rel(1, 'Failure', 'Fantastic Planet')],
      groups: [],
    };
    expect(hasArtistOrAlbumMatch(data, '')).toBe(false);
  });
});

describe('findTrackMatch', () => {
  it('returns the first release with a matching track title', () => {
    const data: SearchResponse = {
      items: [
        rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Hello' }]),
        rel(2, 'B', 'Album B', [{ position: 'B2', title: 'Stuck' }]),
      ],
      groups: [],
    };
    expect(findTrackMatch(data, 'stuck')).toEqual({
      releaseId: 2,
      position: 'B2',
    });
  });

  it('searches grouped releases too', () => {
    const data: SearchResponse = {
      items: [],
      groups: [
        {
          artist: 'X',
          releases: [
            rel(7, 'X', 'Album 7', [{ position: 'C3', title: 'Saturday' }]),
          ],
        },
      ],
    };
    expect(findTrackMatch(data, 'saturday')).toEqual({
      releaseId: 7,
      position: 'C3',
    });
  });

  it('returns null when no track matches', () => {
    const data: SearchResponse = {
      items: [rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Hello' }])],
      groups: [],
    };
    expect(findTrackMatch(data, 'xyzzy')).toBeNull();
  });

  it('returns null for empty needles', () => {
    const data: SearchResponse = {
      items: [rel(1, 'A', 'Album A', [{ position: 'A1', title: 'Stuck' }])],
      groups: [],
    };
    expect(findTrackMatch(data, '')).toBeNull();
  });

  it('matches on raw title even when clean_title is present (matching uses title, not clean_title)', () => {
    // SearchTrack carries clean_title for display only; token-match still
    // keys off the raw title field so the autopilot behaviour is unaffected.
    const trackWithClean: SearchTrack = {
      position: 'A1',
      title: 'Saturday Saviour (2024 Remaster)',
      clean_title: 'Saturday Saviour',
    };
    const data: SearchResponse = {
      items: [{ release_id: 1, artist: 'A', title: 'Album A', tracks: [trackWithClean] }],
      groups: [],
    };
    // Matches on the raw title substring
    expect(findTrackMatch(data, 'remaster')).toEqual({ releaseId: 1, position: 'A1' });
    // clean_title value also happens to match (same word stem)
    expect(findTrackMatch(data, 'saturday')).toEqual({ releaseId: 1, position: 'A1' });
  });
});
