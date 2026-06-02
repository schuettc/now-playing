import { create } from 'zustand';
import type { AlbumStats, NowPlaying } from '@/types';

/**
 * Shared get/set/inflight pattern used by both `fetchAlbumStats` and
 * `fetchAlbumContext`: deduplicate concurrent callers, run the fetch,
 * and commit a *new* Map to the store so Zustand's shallow equality
 * fires for selectors. Failed fetches still cache `null` so panels
 * don't spin forever on a flaky endpoint.
 */
async function cachedFetch<K, V>(args: {
  key: K;
  cache: Map<K, V | null>;
  inflight: Set<K>;
  fetch: () => Promise<V | null>;
  commit: (next: Map<K, V | null>) => void;
  getCache: (s: KioskStore) => Map<K, V | null>;
  get: () => KioskStore;
}): Promise<void> {
  const { key, cache, inflight, fetch, commit, getCache, get } = args;
  if (cache.has(key) || inflight.has(key)) return;
  inflight.add(key);
  try {
    let value: V | null = null;
    try {
      value = await fetch();
    } catch {
      value = null;
    }
    const next = new Map(getCache(get()));
    next.set(key, value);
    commit(next);
  } finally {
    inflight.delete(key);
  }
}

/**
 * Shared kiosk state.
 *
 * Before this store, every panel ran its own `useState`/`useEffect` fetch
 * loop and every WS heartbeat (~1Hz when something is playing) re-ran their
 * effects via the top-down `data` prop. Switching to Zustand with selectors
 * means each panel only re-renders when its slice (album-stats for release
 * 42, context for release 42, etc.) actually changes.
 *
 * The Identify route also gets a home for `searchQuery` and `searchResults`
 * so cross-component nav (e.g. coming back from a kiosk swipe) can preserve
 * the in-progress search.
 */

interface AlbumContext {
  ok: boolean;
  release_id: number;
  summary: string | null;
  url: string | null;
  title: string | null;
}

export interface SearchTrack {
  position?: string;
  title?: string;
  clean_title?: string | null;
}

export interface SearchRelease {
  release_id: number;
  title: string;
  artist: string;
  year?: number;
  label?: string;
  catno?: string;
  art_url?: string;
  tracks?: SearchTrack[];
}

export interface SearchGroup {
  artist: string;
  releases: SearchRelease[];
}

export interface SearchResponse {
  items?: SearchRelease[];
  groups?: SearchGroup[];
}

interface KioskStore {
  // ── Live state from WS ────────────────────────────────────────────────
  payload: NowPlaying | null;
  connected: boolean;
  setPayload: (p: NowPlaying | null) => void;
  setConnected: (c: boolean) => void;

  // ── Per-release fetch caches ──────────────────────────────────────────
  albumStats: Map<number, AlbumStats | null>;
  // Keyed by `rid:<n>` for vinyl releases or `name:<artist>|<album>` for
  // streaming/AirPlay so the same Wikipedia blurb can serve both flows.
  albumContext: Map<string, AlbumContext | null>;
  // In-flight tracking so concurrent panels don't double-fetch the same id.
  _inflightStats: Set<number>;
  _inflightContext: Set<string>;

  fetchAlbumStats: (releaseId: number) => Promise<void>;
  fetchAlbumContext: (
    args: { releaseId?: number; artist?: string; album?: string },
  ) => Promise<void>;

  // ── Identify route state ──────────────────────────────────────────────
  searchQuery: string;
  searchResults: SearchResponse | null;
  setSearchQuery: (q: string) => void;
  setSearchResults: (r: SearchResponse | null) => void;

  // ── Confirm-first UX ──────────────────────────────────────────────────
  /**
   * Monotonically-increasing counter for triggering the LearningChip
   * toast. Surfaces that complete a confirmation tap (GuessConfirm,
   * TappableTrackRow — follow-up features) call `pulseLearningChip()`
   * to fire the chip. Counter pattern (vs. a boolean) so rapid taps
   * cancel-and-replace cleanly via Framer Motion's keyed AnimatePresence.
   */
  learningChipPulses: number;
  pulseLearningChip: () => void;
  /**
   * Wall-clock ms when the current recognition identity
   * `(release_id, track_position, title)` first appeared on the
   * payload. The `SomethingWrongPicker` reads this to render a
   * "X ago" line in the match-details block. Null until the first
   * identified payload arrives.
   */
  lastRecognizedAt: number | null;
  /**
   * Last `/api/pin-track` failure reason (4xx body's `reason` field
   * or `'timeout'` on AbortController fire). `InlineError` reads
   * this slice and auto-dismisses after MOTION.inlineErrorMs.
   * `usePinTrack` sets it on failure; consumers reset to null
   * after rendering.
   */
  pinErrorReason: string | null;
  setPinErrorReason: (reason: string | null) => void;
  /** Optimistic clear of `payload.guess` after a reject/timeout. */
  clearGuess: () => void;
}

type Setter = (partial: Partial<KioskStore>) => void;
type Getter = () => KioskStore;

const initialState = {
  payload: null as NowPlaying | null,
  connected: false,
  albumStats: new Map<number, AlbumStats | null>(),
  albumContext: new Map<string, AlbumContext | null>(),
  _inflightStats: new Set<number>(),
  _inflightContext: new Set<string>(),
  searchQuery: '',
  searchResults: null as SearchResponse | null,
  learningChipPulses: 0,
  lastRecognizedAt: null as number | null,
  pinErrorReason: null as string | null,
};

function recognitionIdentity(p: NowPlaying | null): string | null {
  if (!p) return null;
  if (p.title === undefined && p.release_id === undefined) return null;
  return `${p.release_id ?? 'r'}::${p.track_position ?? 't'}::${p.title ?? '-'}`;
}

// Builds a stable cache key for album-context. Vinyl uses release_id; streaming /
// AirPlay uses (artist, album) which the backend hashes via artcache.key_for so
// the Wikipedia cache file lands in the same dir as the vinyl entries.
export function albumContextKey(args: {
  releaseId?: number;
  artist?: string;
  album?: string;
}): string | null {
  if (args.releaseId !== undefined) return `rid:${args.releaseId}`;
  if (args.artist && args.album) return `name:${args.artist}|${args.album}`;
  return null;
}

const fetchActions = (set: Setter, get: Getter) => ({
  fetchAlbumStats: async (releaseId: number) => {
    const { albumStats, _inflightStats } = get();
    await cachedFetch<number, AlbumStats>({
      key: releaseId,
      cache: albumStats,
      inflight: _inflightStats,
      fetch: async () => {
        const r = await fetch(`/api/album-stats?release_id=${releaseId}`);
        if (!r.ok) return null;
        const body = await r.json().catch(() => null);
        if (body && body.ok === true) {
          return (body.stats as AlbumStats | null) ?? null;
        }
        return null;
      },
      commit: (next) => set({ albumStats: next }),
      getCache: (s) => s.albumStats,
      get,
    });
  },
  fetchAlbumContext: async (args: {
    releaseId?: number;
    artist?: string;
    album?: string;
  }) => {
    const key = albumContextKey(args);
    if (!key) return;
    const { albumContext, _inflightContext } = get();
    await cachedFetch<string, AlbumContext>({
      key,
      cache: albumContext,
      inflight: _inflightContext,
      fetch: async () => {
        const params = new URLSearchParams();
        if (args.releaseId !== undefined) params.set('release_id', String(args.releaseId));
        if (args.artist) params.set('artist', args.artist);
        if (args.album) params.set('album', args.album);
        const r = await fetch(`/api/album-context?${params.toString()}`);
        if (!r.ok) return null;
        return (await r.json().catch(() => null)) as AlbumContext | null;
      },
      commit: (next) => set({ albumContext: next }),
      getCache: (s) => s.albumContext,
      get,
    });
  },
});

const setterActions = (set: Setter, get: Getter) => ({
  setPayload: (p: NowPlaying | null) => {
    const prev = get().payload;
    const prevId = recognitionIdentity(prev);
    const nextId = recognitionIdentity(p);
    if (nextId !== null && nextId !== prevId) {
      set({ payload: p, lastRecognizedAt: Date.now() });
    } else {
      set({ payload: p });
    }
  },
  setConnected: (c: boolean) => set({ connected: c }),
  setSearchQuery: (q: string) => set({ searchQuery: q }),
  setSearchResults: (r: SearchResponse | null) => set({ searchResults: r }),
});

export const useStore = create<KioskStore>((set, get) => ({
  ...initialState,
  ...setterActions(set, get),
  ...fetchActions(set, get),
  pulseLearningChip: () =>
    set((s) => ({ learningChipPulses: s.learningChipPulses + 1 })),
  setPinErrorReason: (reason: string | null) => set({ pinErrorReason: reason }),
  clearGuess: () =>
    set((s) => {
      if (!s.payload || !s.payload.guess) return s;
      const { guess: _drop, ...rest } = s.payload;
      return { payload: rest as typeof s.payload };
    }),
}));
