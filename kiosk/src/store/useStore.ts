import { create } from 'zustand';
import type { AlbumStats, NowPlaying } from '@/types';

/**
 * Shared get/set/inflight pattern used by both `fetchAlbumStats` and
 * `fetchAlbumContext`: deduplicate concurrent callers, run the fetch,
 * and commit a *new* Map to the store so Zustand's shallow equality
 * fires for selectors. A fetch that throws commits nothing, so the next
 * caller tries again and any earlier value stays. With `refresh`, a
 * cached key is fetched again instead of being served from the cache.
 */
async function cachedFetch<K, V>(args: {
  key: K;
  cache: Map<K, V | null>;
  inflight: Set<K>;
  refresh?: boolean;
  fetch: () => Promise<V | null>;
  commit: (next: Map<K, V | null>) => void;
  getCache: (s: KioskStore) => Map<K, V | null>;
  get: () => KioskStore;
}): Promise<void> {
  const { key, cache, inflight, refresh = false, fetch, commit, getCache, get } = args;
  if (inflight.has(key)) return;
  if (cache.has(key) && !refresh) return;
  inflight.add(key);
  try {
    const value = await fetch();
    const next = new Map(getCache(get()));
    next.set(key, value);
    commit(next);
  } catch {
    // Not cached: the next caller retries.
  } finally {
    inflight.delete(key);
  }
}

/** GET a JSON body; throws on a network error, an error status or a non-JSON body. */
async function fetchJson(url: string): Promise<unknown> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
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
    args: { releaseId?: number | null; artist?: string | null; album?: string | null },
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
  /**
   * The guess whose card ran out unanswered. A later payload guessing the
   * same track on the same release arrives without its guess, so the card
   * does not come back. Dropped once another track or release is playing,
   * or nothing is.
   */
  expiredGuess: { releaseId: number | null; position: string } | null;
  /** The card's lifetime ran out: remember the guess and clear it. */
  expireGuess: () => void;
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
  expiredGuess: null as KioskStore['expiredGuess'],
};

function withoutGuess(p: NowPlaying): NowPlaying {
  const { guess: _drop, ...rest } = p;
  return rest as NowPlaying;
}

/**
 * Apply the run-out memory to an incoming payload: returns the payload to
 * show and the memory to keep. The memory lasts only while that track is
 * still the one playing: any other track or release, or a payload that is
 * not PLAYING (needs-id, idle), drops it, so a later play of the track
 * gets its card.
 */
function applyExpiredGuess(
  p: NowPlaying | null,
  expired: KioskStore['expiredGuess'],
): { payload: NowPlaying | null; expiredGuess: KioskStore['expiredGuess'] } {
  if (!expired) return { payload: p, expiredGuess: null };
  const sameTrack =
    p != null &&
    p.state === 'PLAYING' &&
    expired.releaseId === (p.release_id ?? null) &&
    expired.position === p.track_position;
  if (!sameTrack) return { payload: p, expiredGuess: null };
  if (!p.guess) return { payload: p, expiredGuess: expired };
  return p.guess.position === expired.position
    ? { payload: withoutGuess(p), expiredGuess: expired }
    : { payload: p, expiredGuess: null };
}

function recognitionIdentity(p: NowPlaying | null): string | null {
  if (!p) return null;
  if (p.title == null && p.release_id == null) return null;
  return `${p.release_id ?? 'r'}::${p.track_position ?? 't'}::${p.title ?? '-'}`;
}

// Builds a stable cache key for album-context. Vinyl uses release_id; streaming /
// AirPlay uses (artist, album) which the backend hashes via artcache.key_for so
// the Wikipedia cache file lands in the same dir as the vinyl entries.
export function albumContextKey(args: {
  releaseId?: number | null;
  artist?: string | null;
  album?: string | null;
}): string | null {
  if (args.releaseId != null) return `rid:${args.releaseId}`;
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
      // Play counts change while the page stays loaded, so every call
      // refetches; the cached value is shown until the new one lands.
      refresh: true,
      fetch: async () => {
        const body = (await fetchJson(`/api/album-stats?release_id=${releaseId}`)) as {
          ok?: boolean;
          stats?: AlbumStats | null;
        } | null;
        if (body?.ok !== true) throw new Error('album-stats: not ok');
        return body.stats ?? null;
      },
      commit: (next) => set({ albumStats: next }),
      getCache: (s) => s.albumStats,
      get,
    });
  },
  fetchAlbumContext: async (args: { releaseId?: number | null; artist?: string | null; album?: string | null }) => {
    const key = albumContextKey(args);
    if (!key) return;
    const { albumContext, _inflightContext } = get();
    await cachedFetch<string, AlbumContext>({
      key,
      cache: albumContext,
      inflight: _inflightContext,
      fetch: async () => {
        const params = new URLSearchParams();
        if (args.releaseId != null) params.set('release_id', String(args.releaseId));
        if (args.artist) params.set('artist', args.artist);
        if (args.album) params.set('album', args.album);
        return (await fetchJson(`/api/album-context?${params.toString()}`)) as AlbumContext | null;
      },
      commit: (next) => set({ albumContext: next }),
      getCache: (s) => s.albumContext,
      get,
    });
  },
});

const setterActions = (set: Setter, get: Getter) => ({
  setPayload: (incoming: NowPlaying | null) => {
    const prev = get().payload;
    const { payload: p, expiredGuess } = applyExpiredGuess(incoming, get().expiredGuess);
    const prevId = recognitionIdentity(prev);
    const nextId = recognitionIdentity(p);
    if (nextId !== null && nextId !== prevId) {
      set({ payload: p, expiredGuess, lastRecognizedAt: Date.now() });
    } else {
      set({ payload: p, expiredGuess });
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
      return { payload: withoutGuess(s.payload) };
    }),
  expireGuess: () => {
    const p = get().payload;
    if (!p?.guess) return;
    set({
      expiredGuess: { releaseId: p.release_id ?? null, position: p.guess.position },
    });
    get().clearGuess();
  },
}));
