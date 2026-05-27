import { describe, expect, it } from 'vitest';
import { comparePosition } from './positionSort';

const sorted = (positions: string[]): string[] =>
  [...positions].sort(comparePosition);

describe('comparePosition', () => {
  it('sorts numeric portions of a side in numeric order, not lexicographic', () => {
    // The reported bug: B1, B10, B11, B2, B3 should become B1..B11.
    expect(
      sorted(['B1', 'B10', 'B11', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8', 'B9']),
    ).toEqual(['B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8', 'B9', 'B10', 'B11']);
  });

  it('orders sides alphabetically before track number', () => {
    expect(sorted(['B1', 'A1', 'C1', 'D1'])).toEqual(['A1', 'B1', 'C1', 'D1']);
  });

  it('places lettered subtracks immediately after their parent', () => {
    // DJ Shadow Endtroducing pattern.
    expect(sorted(['A2', 'A1', 'A1b', 'A1a'])).toEqual([
      'A1',
      'A1a',
      'A1b',
      'A2',
    ]);
  });

  it('treats dotted subtrack notation (A1.b) the same as A1b', () => {
    expect(sorted(['A1.b', 'A1', 'A1.a', 'A2'])).toEqual([
      'A1',
      'A1.a',
      'A1.b',
      'A2',
    ]);
  });

  it('handles multi-LP cumulative positions like D15', () => {
    expect(sorted(['D15', 'D2', 'D9', 'D10'])).toEqual([
      'D2',
      'D9',
      'D10',
      'D15',
    ]);
  });

  it('does not reorder values across unrelated sides', () => {
    // Sanity: a side-B value should never sort against a side-D value.
    expect(sorted(['D15', 'B5'])).toEqual(['B5', 'D15']);
  });

  it('falls back to localeCompare for unrecognized position shapes', () => {
    // CD1-3 style: regex won't match, so we just need a stable order.
    const a = comparePosition('CD1-3', 'CD1-10');
    expect(a).toBe('CD1-3'.localeCompare('CD1-10'));
  });

  it('compares case-insensitively on side and suffix', () => {
    expect(comparePosition('a1', 'A2')).toBeLessThan(0);
    expect(comparePosition('A1A', 'A1b')).toBeLessThan(0);
  });
});
