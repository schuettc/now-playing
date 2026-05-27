import { useEffect } from 'react';
import { Route, Switch, useLocation, useSearch } from 'wouter';
import { NowPlaying } from '@/components/NowPlaying';
import { LookupView } from '@/routes/lookup';
import { useNowPlaying } from '@/hooks/useNowPlaying';
import { useIdentifyState } from '@/hooks/useIdentifyState';
import { IdentifyProvider } from '@/hooks/identifyContext';
import { useStore } from '@/store/useStore';
import type { FixtureKey } from '@/fixtures';

const VALID_MOCKS: FixtureKey[] = ['vinyl', 'streaming', 'shazam', 'airplay', 'idle'];

/**
 * `/identify` legacy route — redirects to `/lookup` preserving the
 * query string so `?scope=track|album` continues to work via the
 * surviving `useIdentifyScope` hook inside the new variants'
 * `SearchSection`. The bare `/identify` opens the unscoped
 * variant flow.
 */
function IdentifyRedirect() {
  const search = useSearch();
  const [, navigate] = useLocation();
  useEffect(() => {
    const suffix = search ? `?${search}` : '';
    navigate(`/lookup${suffix}`, { replace: true });
  }, [search, navigate]);
  return null;
}

// fallow-ignore-next-line complexity
function resolveMock(): FixtureKey | null {
  const params = new URLSearchParams(window.location.search);
  const q = params.get('mock');
  if (q && (VALID_MOCKS as string[]).includes(q)) return q as FixtureKey;
  const env = import.meta.env.VITE_MOCK as string | undefined;
  if (env && (VALID_MOCKS as string[]).includes(env)) return env as FixtureKey;
  return null;
}

// fallow-ignore-next-line complexity
function resolveWsUrl(): string | undefined {
  const env = import.meta.env.VITE_WS_URL as string | undefined;
  if (env) return env;
  if (typeof window !== 'undefined' && window.location.port !== '5173') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws`;
  }
  return undefined;
}

export default function App() {
  const mock = resolveMock();
  const wsUrl = resolveWsUrl();
  const effectiveMock = mock ?? (wsUrl ? null : 'vinyl');

  // Side-effect-only: push WS payloads into the Zustand store.
  useNowPlaying({
    url: effectiveMock ? undefined : wsUrl,
    mock: effectiveMock,
  });

  // useIdentifyState lives here so the pin lifecycle survives
  // route transitions — without this, navigating /lookup → /
  // after a tap would reset the optimistic "just confirmed ·
  // learning" state. See identify-lookup-view/plan.md § Step 0.
  const payload = useStore((s) => s.payload);
  const identify = useIdentifyState(payload);

  return (
    <IdentifyProvider value={identify}>
      <Switch>
        <Route path="/identify" component={IdentifyRedirect} />
        <Route path="/lookup" component={LookupView} />
        <Route>
          <NowPlaying />
        </Route>
      </Switch>
    </IdentifyProvider>
  );
}
