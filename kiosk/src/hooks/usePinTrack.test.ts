import { afterEach, describe, expect, it, vi } from 'vitest';
import { pinTrackWith } from './usePinTrack';
import { useStore } from '@/store/useStore';

describe('pinTrackWith', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('a stale failure from one row does not revert a newer pin from another row', async () => {
    let failFirst: (err: unknown) => void = () => {};
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => new Promise((_, reject) => { failFirst = reject; }))
      .mockResolvedValueOnce(new Response('{}'));
    vi.stubGlobal('fetch', fetchMock);
    useStore.setState({ pinErrorReason: null });
    // Each tracklist row calls the hook itself; they share only the context.
    const identify = { pin: vi.fn(), clearPin: vi.fn() };

    const a2 = pinTrackWith(identify, { release_id: 1, track_position: 'A2', entry: 'tracklist' });
    const a3 = pinTrackWith(identify, { release_id: 1, track_position: 'A3', entry: 'tracklist' });
    expect((await a3).ok).toBe(true);
    failFirst(new TypeError('network'));
    expect((await a2).ok).toBe(false);

    expect(identify.clearPin).not.toHaveBeenCalled();
    expect(useStore.getState().pinErrorReason).toBeNull();
  });

  it('reverts the pin when the latest request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
    useStore.setState({ pinErrorReason: null });
    const identify = { pin: vi.fn(), clearPin: vi.fn() };

    await pinTrackWith(identify, { release_id: 1, track_position: 'A2', entry: 'tracklist' });

    expect(identify.clearPin).toHaveBeenCalledTimes(1);
    expect(useStore.getState().pinErrorReason).toBe('network');
  });
});
