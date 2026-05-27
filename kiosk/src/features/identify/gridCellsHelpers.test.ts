import { describe, expect, it } from 'vitest';
import { buildGridCells, hoistExpanded } from './gridCellsHelpers';
import type { SearchRelease, SearchResponse } from './types';

const rel = (id: number, artist = 'A', title = `T${id}`): SearchRelease => ({
  release_id: id,
  artist,
  title,
  tracks: [],
});

describe('hoistExpanded', () => {
  it('returns input unchanged when expandedReleaseId is null', () => {
    const list = [rel(1), rel(2)];
    expect(hoistExpanded(list, null)).toBe(list);
  });

  it('returns input unchanged when expanded id is not present', () => {
    const list = [rel(1), rel(2)];
    expect(hoistExpanded(list, 99)).toBe(list);
  });

  it('returns input unchanged when expanded id is already first', () => {
    const list = [rel(1), rel(2)];
    expect(hoistExpanded(list, 1)).toBe(list);
  });

  it('moves the expanded release to the front', () => {
    const a = rel(1);
    const b = rel(2);
    const c = rel(3);
    const out = hoistExpanded([a, b, c], 3);
    expect(out.map((r) => r.release_id)).toEqual([3, 1, 2]);
  });
});

describe('buildGridCells', () => {
  it('returns null when searchResults is null', () => {
    expect(buildGridCells(null, null)).toBeNull();
  });

  it('returns [] when results are empty (no items, no groups)', () => {
    expect(buildGridCells({}, null)).toEqual([]);
    expect(
      buildGridCells({ items: [], groups: [] } as SearchResponse, null),
    ).toEqual([]);
  });

  it('emits header + release cells per group', () => {
    const r1 = rel(1, 'Failure');
    const r2 = rel(2, 'Failure');
    const cells = buildGridCells(
      { items: [r1, r2], groups: [{ artist: 'Failure', releases: [r1, r2] }] },
      null,
    );
    expect(cells).toEqual([
      { kind: 'header', label: 'Failure', count: 2 },
      { kind: 'release', rel: r1 },
      { kind: 'release', rel: r2 },
    ]);
  });

  it('hoists expanded release within its group', () => {
    const r1 = rel(1, 'Failure', 'Comfort');
    const r2 = rel(2, 'Failure', 'Fantastic Planet');
    const cells = buildGridCells(
      { items: [r1, r2], groups: [{ artist: 'Failure', releases: [r1, r2] }] },
      2,
    );
    expect(cells).toEqual([
      { kind: 'header', label: 'Failure', count: 2 },
      { kind: 'release', rel: r2 },
      { kind: 'release', rel: r1 },
    ]);
  });

  it('adds "Other matches" header for leftovers when groups exist', () => {
    const r1 = rel(1, 'Failure');
    const r2 = rel(2, 'Other');
    const cells = buildGridCells(
      { items: [r1, r2], groups: [{ artist: 'Failure', releases: [r1] }] },
      null,
    );
    expect(cells).toEqual([
      { kind: 'header', label: 'Failure', count: 1 },
      { kind: 'release', rel: r1 },
      { kind: 'header', label: 'Other matches', count: 1 },
      { kind: 'release', rel: r2 },
    ]);
  });

  it('omits "Other matches" header when there are no groups', () => {
    const r1 = rel(1, 'A');
    const r2 = rel(2, 'B');
    const cells = buildGridCells({ items: [r1, r2] }, null);
    expect(cells).toEqual([
      { kind: 'release', rel: r1 },
      { kind: 'release', rel: r2 },
    ]);
  });

  it('hoists expanded release within leftovers', () => {
    const r1 = rel(1, 'A');
    const r2 = rel(2, 'B');
    const cells = buildGridCells({ items: [r1, r2] }, 2);
    expect(cells).toEqual([
      { kind: 'release', rel: r2 },
      { kind: 'release', rel: r1 },
    ]);
  });

  it('handles missing releases array on a group', () => {
    const cells = buildGridCells(
      { items: [], groups: [{ artist: 'X' } as never] },
      null,
    );
    expect(cells).toEqual([{ kind: 'header', label: 'X', count: 0 }]);
  });
});
