import { describe, expect, it } from 'vitest';
import { inlineErrorCaption } from './inlineErrorHelpers';

describe('inlineErrorCaption', () => {
  it('maps each documented /api/pin-track reason to a caption', () => {
    expect(inlineErrorCaption('bad-request')).toBe('! couldn\'t pin (bad request)');
    expect(inlineErrorCaption('no-album-locked')).toBe('! album lock changed — try again');
    expect(inlineErrorCaption('release-id-mismatch')).toBe('! album changed — try again');
    expect(inlineErrorCaption('position-not-in-tracklist')).toBe('! track not in this album');
  });

  it('maps timeout and network to the same friendly caption', () => {
    expect(inlineErrorCaption('timeout')).toBe('! couldn\'t reach the kiosk');
    expect(inlineErrorCaption('network')).toBe('! couldn\'t reach the kiosk');
  });

  it('maps select-release-failed to a switch-album caption', () => {
    expect(inlineErrorCaption('select-release-failed')).toBe(
      '! couldn\'t switch album — try again',
    );
  });

  it('falls back to a generic caption on unknown reasons', () => {
    expect(inlineErrorCaption('unknown')).toBe('! couldn\'t pin');
    expect(inlineErrorCaption('')).toBe('! couldn\'t pin');
    expect(inlineErrorCaption('some-future-code')).toBe('! couldn\'t pin');
  });
});
