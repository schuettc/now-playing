import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStore } from './useStore';

function statsResponse(playCount: number): Response {
  return new Response(
    JSON.stringify({
      ok: true,
      stats: { release_id: 42, play_count: playCount, last_played_at: 1, first_played_at: 1 },
    }),
  );
}

describe('fetchAlbumStats', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    useStore.setState({ albumStats: new Map(), albumContext: new Map() });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('does not cache a failed fetch', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('network'));
    await useStore.getState().fetchAlbumStats(42);
    expect(useStore.getState().albumStats.has(42)).toBe(false);

    fetchMock.mockResolvedValueOnce(statsResponse(3));
    await useStore.getState().fetchAlbumStats(42);
    expect(useStore.getState().albumStats.get(42)?.play_count).toBe(3);
  });

  it('does not cache an error status', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 502 }));
    await useStore.getState().fetchAlbumStats(42);
    expect(useStore.getState().albumStats.has(42)).toBe(false);
  });

  it('refetches when asked again and keeps the old value if the refresh fails', async () => {
    fetchMock.mockResolvedValueOnce(statsResponse(3));
    await useStore.getState().fetchAlbumStats(42);
    fetchMock.mockResolvedValueOnce(statsResponse(4));
    await useStore.getState().fetchAlbumStats(42);
    expect(useStore.getState().albumStats.get(42)?.play_count).toBe(4);

    fetchMock.mockRejectedValueOnce(new TypeError('network'));
    await useStore.getState().fetchAlbumStats(42);
    expect(useStore.getState().albumStats.get(42)?.play_count).toBe(4);
  });
});

describe('fetchAlbumContext', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    useStore.setState({ albumStats: new Map(), albumContext: new Map() });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('does not cache a failed fetch, and fetches a cached album once', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 502 }));
    await useStore.getState().fetchAlbumContext({ releaseId: 42 });
    expect(useStore.getState().albumContext.has('rid:42')).toBe(false);

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, release_id: 42, url: 'https://w/x' })),
    );
    await useStore.getState().fetchAlbumContext({ releaseId: 42 });
    await useStore.getState().fetchAlbumContext({ releaseId: 42 });
    expect(useStore.getState().albumContext.get('rid:42')?.url).toBe('https://w/x');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
