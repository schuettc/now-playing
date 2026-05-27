import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { formatRelativeTime } from './format';

// Fixed reference instant: 2026-01-15T12:00:00Z.
const NOW_SEC = 1768521600;

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW_SEC * 1000));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "just now" for diffs under 60 seconds', () => {
    expect(formatRelativeTime(NOW_SEC)).toBe('just now');
    expect(formatRelativeTime(NOW_SEC - 30)).toBe('just now');
    expect(formatRelativeTime(NOW_SEC - 59)).toBe('just now');
  });

  it('uses singular "1 minute ago" for exactly 1 minute', () => {
    expect(formatRelativeTime(NOW_SEC - 60)).toBe('1 minute ago');
  });

  it('renders minutes plural', () => {
    expect(formatRelativeTime(NOW_SEC - 300)).toBe('5 minutes ago');
  });

  it('uses singular "1 hour ago" for exactly 1 hour', () => {
    expect(formatRelativeTime(NOW_SEC - 3600)).toBe('1 hour ago');
  });

  it('renders hours plural', () => {
    expect(formatRelativeTime(NOW_SEC - 7200)).toBe('2 hours ago');
  });

  it('renders days plural', () => {
    expect(formatRelativeTime(NOW_SEC - 86400 * 3)).toBe('3 days ago');
  });

  it('renders weeks plural', () => {
    expect(formatRelativeTime(NOW_SEC - 86400 * 14)).toBe('2 weeks ago');
  });

  it('renders months plural', () => {
    // ~5 months — pick a value safely past the weeks bucket and below 2y.
    expect(formatRelativeTime(NOW_SEC - 86400 * 30 * 5)).toBe('5 months ago');
  });

  it('renders years for diffs at or past 2 years', () => {
    expect(formatRelativeTime(NOW_SEC - 86400 * 365 * 2)).toBe('2 years ago');
  });

  it('renders years for older diffs', () => {
    // The years bucket only fires past 2y (threshold). A "1 year ago"
    // string is unreachable in this formatter — values around 1y fall
    // into the months bucket and render as "12 months ago". This
    // matches the pre-refactor behavior exactly.
    expect(formatRelativeTime(NOW_SEC - 86400 * 365)).toBe('12 months ago');
    expect(formatRelativeTime(NOW_SEC - 86400 * 365 * 3)).toBe('3 years ago');
  });

  it('clamps future timestamps to "just now"', () => {
    expect(formatRelativeTime(NOW_SEC + 3600)).toBe('just now');
    expect(formatRelativeTime(NOW_SEC + 86400 * 365)).toBe('just now');
  });
});
