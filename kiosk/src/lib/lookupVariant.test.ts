import { describe, expect, it } from 'vitest';
import { pickLookupVariant } from './lookupVariant';

describe('pickLookupVariant', () => {
  it('returns search-first when recents is null (loading)', () => {
    expect(pickLookupVariant(null)).toBe('search-first');
  });

  it('returns search-first when recents is empty', () => {
    expect(pickLookupVariant([])).toBe('search-first');
  });

  it('returns hybrid for 1-4 recents', () => {
    expect(pickLookupVariant([1])).toBe('hybrid');
    expect(pickLookupVariant([1, 2, 3, 4])).toBe('hybrid');
  });

  it('returns recents-first for 5+ recents', () => {
    expect(pickLookupVariant([1, 2, 3, 4, 5])).toBe('recents-first');
    expect(pickLookupVariant([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])).toBe('recents-first');
  });
});
