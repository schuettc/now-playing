import { useEffect, useRef } from 'react';
import type { WireMessage } from '@/types';
import { fixtures, type FixtureKey } from '@/fixtures';
import { useStore } from '@/store/useStore';

interface Options {
  url?: string;
  mock?: FixtureKey | null;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

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

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(url);
      ws.onopen = () => {
        attempt.current = 0;
        setConnected(true);
      };
      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        const delay = Math.min(
          RECONNECT_MAX_MS,
          RECONNECT_BASE_MS * 2 ** attempt.current,
        );
        attempt.current += 1;
        timer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as WireMessage;
          if (msg.type === 'now_playing') {
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
