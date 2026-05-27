import { describe, expect, it } from 'vitest';
import type { QueueItem } from '@/types';
import { sliceQueueWindow } from './queue';

const q = (titles: Array<string | null>): QueueItem[] =>
  titles.map((title) => ({ title, artist: null, album: null }));

describe('sliceQueueWindow', () => {
  it('returns empty rows for an empty queue', () => {
    expect(sliceQueueWindow([], 0)).toEqual([]);
    expect(sliceQueueWindow([], -1)).toEqual([]);
  });

  it('shows the head as upcoming when currentIndex is negative', () => {
    const items = q(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']);
    const rows = sliceQueueWindow(items, -1);
    expect(rows.map((r) => r.title)).toEqual([
      'a',
      'b',
      'c',
      'd',
      'e',
      'f',
    ]);
    expect(rows.every((r) => !r.isCurrent && !r.isDimmed)).toBe(true);
    expect(rows.map((r) => r.position)).toEqual([
      '1',
      '2',
      '3',
      '4',
      '5',
      '6',
    ]);
  });

  it('builds recent (dimmed) + current + upcoming around currentIndex', () => {
    const items = q(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']);
    const rows = sliceQueueWindow(items, 4);
    expect(rows.map((r) => r.title)).toEqual([
      'b',
      'c',
      'd',
      'e',
      'f',
      'g',
      'h',
      'i',
      'j',
    ]);
    expect(rows.filter((r) => r.isDimmed).map((r) => r.title)).toEqual([
      'b',
      'c',
      'd',
    ]);
    expect(rows.find((r) => r.isCurrent)?.title).toBe('e');
    expect(rows.find((r) => r.isCurrent)?.position).toBe('5');
  });

  it('clamps recent slice when currentIndex is near the start', () => {
    const items = q(['a', 'b', 'c', 'd', 'e']);
    const rows = sliceQueueWindow(items, 1);
    expect(rows.map((r) => r.title)).toEqual(['a', 'b', 'c', 'd', 'e']);
    expect(rows[0].isDimmed).toBe(true);
    expect(rows[1].isCurrent).toBe(true);
  });

  it('renders an "Unknown" title for null entries', () => {
    const items = q([null, null, null]);
    const rows = sliceQueueWindow(items, 1);
    expect(rows.map((r) => r.title)).toEqual(['Unknown', 'Unknown', 'Unknown']);
  });

  it('caps upcoming at UPCOMING_COUNT (6)', () => {
    const items = q(Array.from({ length: 20 }, (_, i) => `t${i}`));
    const rows = sliceQueueWindow(items, 0);
    const upcoming = rows.filter((r) => !r.isDimmed && !r.isCurrent);
    expect(upcoming).toHaveLength(6);
    expect(upcoming.map((r) => r.title)).toEqual([
      't1',
      't2',
      't3',
      't4',
      't5',
      't6',
    ]);
  });

  it('emits stable keys based on absolute queue position', () => {
    const items = q(['a', 'b', 'c', 'd']);
    const rows = sliceQueueWindow(items, 2);
    expect(rows.map((r) => r.key)).toEqual(['q_0', 'q_1', 'q_2', 'q_3']);
  });
});
