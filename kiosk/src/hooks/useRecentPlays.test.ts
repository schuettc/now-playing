import { describe, expect, it } from 'vitest';
import { rowToRecent } from './useRecentPlays';

describe('rowToRecent', () => {
  it('maps started_at to ts (unix seconds, no conversion)', () => {
    expect(
      rowToRecent({
        release_id: 100, track_position: 'A1', artist: 'X', title: 'Y',
        album: 'Z', match_method: 'shazam', source: 'vinyl',
        started_at: 1700000000, ended_at: 1700000300,
      }).ts,
    ).toBe(1700000000);
  });

  it('derives art_url when release_id present', () => {
    expect(
      rowToRecent({
        release_id: 42, track_position: null, artist: null, title: null,
        album: null, match_method: null, source: null,
        started_at: 0, ended_at: 0,
      }).art_url,
    ).toBe('/art/42');
  });

  it('leaves art_url undefined when release_id is null', () => {
    expect(
      rowToRecent({
        release_id: null, track_position: null, artist: 'A', title: 'T',
        album: null, match_method: null, source: 'airplay',
        started_at: 0, ended_at: 0,
      }).art_url,
    ).toBeUndefined();
  });

  it('preserves all non-derived fields', () => {
    const r = rowToRecent({
      release_id: 1, track_position: 'B2', artist: 'A', title: 'T',
      album: 'AL', match_method: 'fingerprint', source: 'vinyl',
      started_at: 100, ended_at: 200,
    });
    expect(r.artist).toBe('A');
    expect(r.title).toBe('T');
    expect(r.album).toBe('AL');
    expect(r.match_method).toBe('fingerprint');
    expect(r.source).toBe('vinyl');
    expect(r.track_position).toBe('B2');
  });
});
