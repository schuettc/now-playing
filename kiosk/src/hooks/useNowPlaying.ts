import { useEffect, useRef } from 'react';
import type { NowPlaying, WireMessage } from '@/types';
import { fixtures, type FixtureKey } from '@/fixtures';
import { useStore } from '@/store/useStore';

interface Options {
  url?: string;
  mock?: FixtureKey | null;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

/**
 * Bring the store in line with the backend after a reconnect. The server
 * replays its last payload on connect, but a restarted backend has none
 * and sends nothing, which would leave the pre-restart track on screen.
 * `GET /api/now-playing` answers either way: a null payload means idle.
 * The snapshot is dropped if the socket delivered a payload while it was
 * in flight, since that one is at least as new.
 */
export async function resyncFromSnapshot(
  superseded: () => boolean,
  setPayload: (p: NowPlaying | null) => void,
): Promise<void> {
  let body: { ok?: boolean; payload?: NowPlaying | null };
  try {
    const r = await fetch('/api/now-playing', { cache: 'no-store' });
    if (!r.ok) return;
    body = await r.json();
  } catch {
    return;
  }
  if (body.ok !== true || superseded()) return;
  setPayload(body.payload ?? null);
}

/**
 * Subscribes the WebSocket payload stream into the global Zustand store.
 *
 * Returns no value — components read `payload` / `connected` from the store
 * via selectors so re-renders are scoped to the slice each one cares about
 * (album-stats vs. tracklist vs. context vs. raw playback state).
 */
export function useNowPlaying({ url, mock }: Options): void {
  const setPayload = useStore((s) => s.setPayload);
  const setConnected = useStore((s) => s.setConnected);
  const attempt = useRef(0);

  useEffect(() => {
    if (mock) {
      setPayload(fixtures[mock] ?? null);
      setConnected(false);
      return;
    }
    if (!url) return;

    let ws: WebSocket | null = null;
    let timer: number | null = null;
    let cancelled = false;
    let hasClosed = false;

    const connect = () => {
      if (cancelled) return;
      const socket = new WebSocket(url);
      ws = socket;
      let gotPayload = false;
      socket.onopen = () => {
        attempt.current = 0;
        setConnected(true);
        if (hasClosed) {
          void resyncFromSnapshot(() => cancelled || gotPayload || ws !== socket, setPayload);
        }
      };
      socket.onclose = () => {
        hasClosed = true;
        setConnected(false);
        if (cancelled) return;
        const delay = Math.min(
          RECONNECT_MAX_MS,
          RECONNECT_BASE_MS * 2 ** attempt.current,
        );
        attempt.current += 1;
        timer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => socket.close();
      socket.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as WireMessage;
          if (msg.type === 'now_playing') {
            gotPayload = true;
            setPayload(msg.payload);
          }
        } catch {
          // ignore malformed frames
        }
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, [url, mock, setPayload, setConnected]);
}
