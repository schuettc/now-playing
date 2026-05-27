import { describe, expect, it } from 'vitest';
import { albumCardSize } from './AlbumCard';
import { searchFieldShowsClear } from './SearchField';

describe('albumCardSize', () => {
  it('maps tokens to design-spec px dimensions', () => {
    expect(albumCardSize('sm')).toBe(140);
    expect(albumCardSize('md')).toBe(180);
    expect(albumCardSize('lg')).toBe(220);
  });
});

describe('searchFieldShowsClear', () => {
  it('hides clear button on empty', () => {
    expect(searchFieldShowsClear('')).toBe(false);
  });

  it('shows clear button on any non-empty value', () => {
    expect(searchFieldShowsClear('q')).toBe(true);
    expect(searchFieldShowsClear('  ')).toBe(true);
  });
});
