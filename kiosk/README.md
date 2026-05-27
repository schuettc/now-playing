# Kiosk

React + Vite + Tailwind + Zustand single-page app that renders the
now-playing display and the manual `/identify` browse-and-pick flow.
Talks to the Python orchestrator over a WebSocket for live state and
over HTTP for one-shot endpoints (`/api/now-playing`,
`/api/collection/search`, `/api/identify`, `/api/album-stats`,
`/api/album-context`, `/api/art-candidates`, `/api/art-override`).

The kiosk's `dist/` is committed because the target Raspberry Pi has
no Node toolchain — it consumes the pre-built bundle directly via
Chromium kiosk mode. Rebuild on a dev machine (`pnpm build`) before
pushing.

## Folder map

```
src/
├── routes/             Thin route shells (one per top-level URL).
│                       No business logic — just compose hooks +
│                       feature components.
│
├── features/<domain>/  Domain-grouped UI + hooks. Each domain owns
│                       its own state, fetching, and component tree.
│                       Cross-route reuse pulls types/components
│                       out into smaller modules; don't reach across
│                       feature folders.
│   ├── identify/       Manual /identify route: search controller,
│   │                   scope prefill, submit, result-grid cells,
│   │                   album cards, tracklist picker.
│   └── admin/          Album-art picker (modal triggered from the
│                       NowPlaying admin overlay).
│
├── components/         Presentational components used across routes.
│   │                   No fetch calls, no useEffect against the
│   │                   network — those live in features/<domain>/
│   │                   or the store.
│   └── now-playing/    Sub-components specific to the NowPlaying
│                       route shell (split out for size, not
│                       reuse).
│
├── hooks/              Cross-cutting hooks
│                       (useLongPress, useScreenState,
│                       useArtPrefetch, useArtOverride, useClock,
│                       useDominantColor, useNowPlaying).
│
├── store/              Zustand store. WS-backed live state,
│                       per-release caches (album stats, album
│                       context), and the Identify route's search
│                       slice.
│
├── lib/                Pure helpers (art identity + cache-busting,
│                       tracklist side derivation, cn utility).
│                       Add unit tests in *.test.ts next to the file.
│
├── utils/              Formatters (formatDuration,
│                       formatRelativeTime).
│
├── fixtures/           Mock now-playing payloads for ?mock=… dev
│                       URLs.
│
└── types.ts            Shared TypeScript types (NowPlaying,
                        Source, MatchMethod, TracklistItem,
                        QueueItem, …).
```

## One architectural rule

**Fetching belongs in the store or a feature hook, not inline in a
component.** Components that call `fetch()` or open `EventSource`
inside `useEffect` are a smell — they're hard to test, hard to share,
and they hide the network surface of the app. If you find yourself
reaching for an effect-with-fetch in a component file, lift it into
`store/` (if multiple panels need the same data) or
`features/<domain>/use<Thing>.ts` (if it's route-local).

## Scripts

```sh
pnpm dev         # local dev server on http://localhost:5173
pnpm build       # production build → dist/
pnpm preview     # serve the production bundle locally
pnpm typecheck   # tsc --noEmit
pnpm test        # vitest (unit tests for pure helpers)
```
