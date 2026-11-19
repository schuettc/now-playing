import { describe, expect, it } from 'vitest';
import {
  artLoadReducer,
  initialArtLoad,
  loadingUrlOf,
  retryDelayOf,
  showsNoArt,
  type ArtLoadState,
} from './artLoad';
import { swapSrcInPlace } from './useCrossfadePair';

/** Fail the URL currently loading, then fire the retry timer. */
function failAndRetry(s: ArtLoadState): ArtLoadState {
  const failed = artLoadReducer(s, { type: 'error', url: loadingUrlOf(s)! });
  return artLoadReducer(failed, { type: 'retry' });
}

describe('artLoadReducer', () => {
  it('shows nothing, and no NO ART tile, while the first load is in flight', () => {
    const s = initialArtLoad('/art-cache/a');
    expect(loadingUrlOf(s)).toBe('/art-cache/a');
    expect(s.shown).toBeNull();
    expect(showsNoArt(s)).toBe(false);
  });

  it('shows the image once it has loaded', () => {
    const s = artLoadReducer(initialArtLoad('/art-cache/a'), {
      type: 'loaded',
      url: '/art-cache/a',
    });
    expect(s.shown).toBe('/art-cache/a');
    expect(loadingUrlOf(s)).toBeNull();
  });

  it('keeps the loaded image on screen while a new URL loads, then swaps', () => {
    let s = artLoadReducer(initialArtLoad('/art-cache/a'), {
      type: 'loaded',
      url: '/art-cache/a',
    });
    s = artLoadReducer(s, { type: 'target', src: '/art-cache/b' });
    expect(s.shown).toBe('/art-cache/a');
    expect(loadingUrlOf(s)).toBe('/art-cache/b');
    s = artLoadReducer(s, { type: 'loaded', url: '/art-cache/b' });
    expect(s.shown).toBe('/art-cache/b');
  });

  it('ignores a load result for a URL it is no longer loading', () => {
    let s = initialArtLoad('/art-cache/a');
    s = artLoadReducer(s, { type: 'target', src: '/art-cache/b' });
    expect(artLoadReducer(s, { type: 'loaded', url: '/art-cache/a' })).toBe(s);
    expect(artLoadReducer(s, { type: 'error', url: '/art-cache/a' })).toBe(s);
  });

  it('shows NO ART on a failed first load and retries with a cache-busted URL', () => {
    let s = initialArtLoad('/art-cache/a?u=x');
    s = artLoadReducer(s, { type: 'error', url: '/art-cache/a?u=x' });
    expect(showsNoArt(s)).toBe(true);
    expect(retryDelayOf(s)).toBe(5000);
    s = artLoadReducer(s, { type: 'retry' });
    expect(loadingUrlOf(s)).toBe('/art-cache/a?u=x&v=1');
    s = artLoadReducer(s, { type: 'loaded', url: '/art-cache/a?u=x&v=1' });
    expect(showsNoArt(s)).toBe(false);
    expect(s.shown).toBe('/art-cache/a?u=x&v=1');
  });

  it('ends on NO ART, not the stale image, when every retry fails', () => {
    let s = artLoadReducer(initialArtLoad('/art-cache/a'), {
      type: 'loaded',
      url: '/art-cache/a',
    });
    s = artLoadReducer(s, { type: 'target', src: '/art-cache/gone' });
    s = failAndRetry(failAndRetry(failAndRetry(s)));
    expect(s.shown).toBe('/art-cache/a');
    s = artLoadReducer(s, { type: 'error', url: loadingUrlOf(s)! });
    expect(s.shown).toBeNull();
    expect(showsNoArt(s)).toBe(true);
    expect(retryDelayOf(s)).toBeNull();
    expect(loadingUrlOf(s)).toBeNull();
  });

  it('gives a new URL the full retry budget', () => {
    let s = failAndRetry(failAndRetry(initialArtLoad('/art-cache/a')));
    s = artLoadReducer(s, { type: 'target', src: '/art-cache/b' });
    expect(loadingUrlOf(s)).toBe('/art-cache/b');
    s = artLoadReducer(s, { type: 'error', url: '/art-cache/b' });
    expect(retryDelayOf(s)).toBe(5000);
  });
});

describe('swapSrcInPlace', () => {
  it('takes a changed src when the identity is unchanged', () => {
    const layer = { identity: 'Artist|#0', src: '/art-cache/a' };
    expect(swapSrcInPlace(layer, 'Artist|#0', '/art-cache/b')).toEqual({
      identity: 'Artist|#0',
      src: '/art-cache/b',
    });
  });

  it('takes art that arrives after a payload with none', () => {
    const layer = { identity: 'Artist|Album#0', src: undefined };
    expect(swapSrcInPlace(layer, 'Artist|Album#0', '/art-cache/a').src).toBe('/art-cache/a');
  });

  it('leaves the layer alone when the src is the same or the identity moved on', () => {
    const layer = { identity: 'release:1#0', src: '/art/1' };
    expect(swapSrcInPlace(layer, 'release:1#0', '/art/1')).toBe(layer);
    expect(swapSrcInPlace(layer, 'release:2#0', '/art/2')).toBe(layer);
  });
});
