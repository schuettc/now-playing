import { describe, expect, it } from 'vitest';
import { buildSomethingWrongRows } from './somethingWrongPickerRows';
import type { NowPlaying } from '@/types';

function base(over: Partial<NowPlaying> = {}): NowPlaying {
  return {
    ts: '2026-01-01T00:00:00Z',
    state: 'PLAYING',
    source: 'vinyl',
    title: 'Track',
    ...over,
  } as NowPlaying;
}

const TWO_TRACKS = [
  { position: 'A1', title: 'X', side: 'A', duration_seconds: 180 },
  { position: 'A2', title: 'Y', side: 'A', duration_seconds: 200 },
];

describe('buildSomethingWrongRows', () => {
  it('includes wrong-song on vinyl (recognition can be wrong)', () => {
    const rows = buildSomethingWrongRows(base(), false);
    expect(rows.map((r) => r.kind)).toContain('wrong-song');
  });

  it('hides wrong-song on airplay (source metadata is authoritative)', () => {
    const rows = buildSomethingWrongRows(base({ source: 'airplay' }), false);
    expect(rows.map((r) => r.kind)).not.toContain('wrong-song');
  });

  it('hides wrong-song on streaming (source metadata is authoritative)', () => {
    const rows = buildSomethingWrongRows(base({ source: 'streaming' }), false);
    expect(rows.map((r) => r.kind)).not.toContain('wrong-song');
  });

  it('hides wrong-track when tracklist is missing or length <= 1', () => {
    expect(buildSomethingWrongRows(base(), false).map((r) => r.kind)).not.toContain(
      'wrong-track',
    );
    const single = base({ tracklist: [{ position: 'A1', title: 'X', side: 'A', duration_seconds: 180 }] });
    expect(buildSomethingWrongRows(single, false).map((r) => r.kind)).not.toContain(
      'wrong-track',
    );
  });

  it('shows wrong-track when tracklist has > 1 entry on vinyl with a release_id', () => {
    const data = base({ tracklist: TWO_TRACKS, release_id: 1 });
    expect(buildSomethingWrongRows(data, false).map((r) => r.kind)).toContain(
      'wrong-track',
    );
  });

  it('hides wrong-track without a release_id even on vinyl (rows would not be tappable)', () => {
    const data = base({ tracklist: TWO_TRACKS });
    expect(buildSomethingWrongRows(data, false).map((r) => r.kind)).not.toContain(
      'wrong-track',
    );
  });

  it('hides wrong-track on non-vinyl sources even with a tracklist and release_id', () => {
    const data = base({
      source: 'airplay',
      tracklist: TWO_TRACKS,
      release_id: 1,
    });
    expect(buildSomethingWrongRows(data, false).map((r) => r.kind)).not.toContain(
      'wrong-track',
    );
  });

  it('shows wrong-album when alternates are present (vinyl only)', () => {
    const data = base({
      alternate_releases: [{ release_id: 9, album: 'Alt' }],
    });
    expect(buildSomethingWrongRows(data, false).map((r) => r.kind)).toContain(
      'wrong-album',
    );
  });

  it('hides wrong-album on non-vinyl sources', () => {
    const data = base({
      source: 'airplay',
      alternate_releases: [{ release_id: 9, album: 'Alt' }],
    });
    expect(buildSomethingWrongRows(data, false).map((r) => r.kind)).not.toContain(
      'wrong-album',
    );
  });

  it('shows change-art only when canChangeArt and a target exists', () => {
    expect(
      buildSomethingWrongRows(base({ release_id: 1 }), true).map((r) => r.kind),
    ).toContain('change-art');
    expect(
      buildSomethingWrongRows(base({ album: 'A' }), true).map((r) => r.kind),
    ).toContain('change-art');
    expect(
      buildSomethingWrongRows(base({ release_id: 1 }), false).map((r) => r.kind),
    ).not.toContain('change-art');
    expect(buildSomethingWrongRows(base(), true).map((r) => r.kind)).not.toContain(
      'change-art',
    );
  });

  it('orders rows: wrong-track, wrong-album, wrong-song, change-art', () => {
    const data = base({
      tracklist: TWO_TRACKS,
      alternate_releases: [{ release_id: 9, album: 'Alt' }],
      release_id: 1,
    });
    const kinds = buildSomethingWrongRows(data, true).map((r) => r.kind);
    expect(kinds).toEqual(['wrong-track', 'wrong-album', 'wrong-song', 'change-art']);
  });

  describe('clear-fingerprints', () => {
    it('hides the row when learned_fingerprint_count is missing', () => {
      expect(
        buildSomethingWrongRows(base({ release_id: 1 }), true).map((r) => r.kind),
      ).not.toContain('clear-fingerprints');
    });

    it('hides the row when learned_fingerprint_count is 0', () => {
      expect(
        buildSomethingWrongRows(
          base({ release_id: 1, learned_fingerprint_count: 0 }),
          true,
        ).map((r) => r.kind),
      ).not.toContain('clear-fingerprints');
    });

    it('shows the row when learned_fingerprint_count > 0', () => {
      expect(
        buildSomethingWrongRows(
          base({ release_id: 1, learned_fingerprint_count: 7 }),
          true,
        ).map((r) => r.kind),
      ).toContain('clear-fingerprints');
    });

    it('appears after change-art (destructive last)', () => {
      const data = base({
        release_id: 1,
        album: 'A',
        learned_fingerprint_count: 3,
      });
      const kinds = buildSomethingWrongRows(data, true).map((r) => r.kind);
      const artIdx = kinds.indexOf('change-art');
      const clearIdx = kinds.indexOf('clear-fingerprints');
      expect(artIdx).toBeGreaterThanOrEqual(0);
      expect(clearIdx).toBeGreaterThan(artIdx);
    });
  });
});
