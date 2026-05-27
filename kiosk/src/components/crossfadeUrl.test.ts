import { describe, expect, it } from 'vitest';
import { appendAttemptSuffix } from './crossfadeUrl';

describe('appendAttemptSuffix', () => {
  it('returns undefined when src is undefined', () => {
    expect(appendAttemptSuffix(undefined, 0)).toBeUndefined();
    expect(appendAttemptSuffix(undefined, 3)).toBeUndefined();
  });

  it('returns src unchanged when attempt is 0', () => {
    expect(appendAttemptSuffix('/art/abc', 0)).toBe('/art/abc');
    expect(appendAttemptSuffix('/art/abc?x=1', 0)).toBe('/art/abc?x=1');
  });

  it('appends ?v=N when src has no query string', () => {
    expect(appendAttemptSuffix('/art/abc', 1)).toBe('/art/abc?v=1');
    expect(appendAttemptSuffix('/art/abc', 2)).toBe('/art/abc?v=2');
  });

  it('appends &v=N when src already has a query string', () => {
    expect(appendAttemptSuffix('/art/abc?x=1', 1)).toBe('/art/abc?x=1&v=1');
    expect(appendAttemptSuffix('https://e.io/x?a=b&c=d', 3)).toBe(
      'https://e.io/x?a=b&c=d&v=3',
    );
  });
});
