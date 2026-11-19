import { describe, expect, it } from 'vitest';
import { modalResetKey } from './useAdminModals';

describe('modalResetKey', () => {
  it('changes when the track changes', () => {
    expect(modalResetKey('release:1#One', 'track')).not.toBe(
      modalResetKey('release:1#Two', 'track'),
    );
  });

  it('changes when the screen goes idle on the same payload', () => {
    expect(modalResetKey('release:1#One', 'track')).not.toBe(
      modalResetKey('release:1#One', 'idle'),
    );
  });

  it('is stable across republishes and pauses of the same track', () => {
    expect(modalResetKey('release:1#One', 'track')).toBe(
      modalResetKey('release:1#One', 'needs-id'),
    );
  });
});
