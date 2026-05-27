import { describe, expect, it } from 'vitest';
import { pillContent } from './StatusPill';

describe('pillContent', () => {
  it('confirmed-shazam → green dot, source label, matched sub, no pulse', () => {
    const c = pillContent('vinyl', 'confirmed-shazam');
    expect(c.label).toBe('VINYL');
    expect(c.sub).toBe('Shazam · matched');
    expect(c.dot).toBe('var(--dot-ok)');
    expect(c.pulse).toBe(false);
  });

  it('confirmed-local → green dot, fingerprint sub', () => {
    const c = pillContent('vinyl', 'confirmed-local');
    expect(c.sub).toBe('Fingerprint · remembered');
    expect(c.dot).toBe('var(--dot-ok)');
    expect(c.pulse).toBe(false);
  });

  it('awaiting-confirm → amber pulse, expanded label "VINYL · BEST GUESS", no sub', () => {
    const c = pillContent('vinyl', 'awaiting-confirm');
    expect(c.label).toBe('VINYL · BEST GUESS');
    // No sub — the inline confirm card below TrackInfo carries the
    // affordance and the title; a pill sub would duplicate it.
    expect(c.sub).toBe('');
    expect(c.dot).toBe('var(--dot-wait)');
    expect(c.pulse).toBe(true);
  });

  it('identifying → amber pulse, source label only, identifying… sub', () => {
    const c = pillContent('vinyl', 'identifying');
    expect(c.label).toBe('VINYL');
    expect(c.sub).toBe('identifying…');
    expect(c.dot).toBe('var(--dot-wait)');
    expect(c.pulse).toBe(true);
  });

  it('user-pinned → violet dot, JUST CONFIRMED label, learning sub', () => {
    const c = pillContent('vinyl', 'user-pinned');
    expect(c.label).toBe('JUST CONFIRMED');
    expect(c.sub).toBe('learning…');
    expect(c.dot).toBe('var(--dot-user)');
    expect(c.pulse).toBe(false);
  });

  it('needs-id → grey dot, unknown sub', () => {
    const c = pillContent('vinyl', 'needs-id');
    expect(c.label).toBe('VINYL');
    expect(c.sub).toBe('Unknown · help identify');
    expect(c.dot).toBe('var(--dot-idle)');
    expect(c.pulse).toBe(false);
  });

  it('non-vinyl sources suppress the method-label sub', () => {
    // Sonos provides metadata for airplay/streaming directly — the
    // vinyl-cascade method label is misleading for those sources.
    expect(pillContent('airplay', 'confirmed-shazam').sub).toBe('');
    expect(pillContent('streaming', 'confirmed-shazam').sub).toBe('');
    expect(pillContent('streaming', 'confirmed-local').sub).toBe('');
    // Vinyl still shows the sub.
    expect(pillContent('vinyl', 'confirmed-shazam').sub).toBe('Shazam · matched');
  });

  it('awaiting-confirm and identifying pulse; all other states hold steady', () => {
    expect(pillContent('vinyl', 'confirmed-shazam').pulse).toBe(false);
    expect(pillContent('vinyl', 'confirmed-local').pulse).toBe(false);
    expect(pillContent('vinyl', 'awaiting-confirm').pulse).toBe(true);
    expect(pillContent('vinyl', 'identifying').pulse).toBe(true);
    expect(pillContent('vinyl', 'user-pinned').pulse).toBe(false);
    expect(pillContent('vinyl', 'needs-id').pulse).toBe(false);
  });
});
