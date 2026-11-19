import { afterEach, describe, expect, it, vi } from 'vitest';
import { resyncFromSnapshot } from './useNowPlaying';

function snapshot(payload: unknown): Response {
  return new Response(JSON.stringify({ ok: true, payload }));
}

describe('resyncFromSnapshot', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('goes idle when a restarted backend has published nothing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(snapshot(null)));
    const setPayload = vi.fn();
    await resyncFromSnapshot(() => false, setPayload);
    expect(setPayload).toHaveBeenCalledWith(null);
  });

  it('applies the snapshot payload', async () => {
    const payload = { ts: 't', state: 'PLAYING', source: 'airplay', title: 'X' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(snapshot(payload)));
    const setPayload = vi.fn();
    await resyncFromSnapshot(() => false, setPayload);
    expect(setPayload).toHaveBeenCalledWith(payload);
  });

  it('drops the snapshot when the socket has delivered a payload meanwhile', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(snapshot(null)));
    const setPayload = vi.fn();
    await resyncFromSnapshot(() => true, setPayload);
    expect(setPayload).not.toHaveBeenCalled();
  });

  it('keeps the current payload when the snapshot cannot be read', async () => {
    const setPayload = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
    await resyncFromSnapshot(() => false, setPayload);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 502 })));
    await resyncFromSnapshot(() => false, setPayload);
    expect(setPayload).not.toHaveBeenCalled();
  });
});
