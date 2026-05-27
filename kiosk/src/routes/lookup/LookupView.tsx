import { useEffect, useRef, useState } from 'react';
import { useLocation, useSearch } from 'wouter';
import { useStore } from '@/store/useStore';
import { useRecentPlays } from '@/hooks/useRecentPlays';
import { useReleaseTracklist } from '@/hooks/useReleaseTracklist';
import { pickLookupVariant, type LookupVariant } from '@/lib/lookupVariant';
import { parseFromNeedsId } from '@/features/identify/identifyScopeHelpers';
import type { RecentPlay } from '@/hooks/useRecentPlays';
import type { TracklistItem } from '@/types';
import { track as telemetryTrack } from '@/lib/telemetry';
import { Centered } from './shared';
import { PickedProvider } from './pickedContext';
import { LookupViewScoped } from './LookupViewScoped';
import { LookupViewSearchFirst } from './LookupViewSearchFirst';
import { LookupViewRecentsFirst } from './LookupViewRecentsFirst';
import { LookupViewHybrid } from './LookupViewHybrid';
import { pickScopedProps } from './scopedProps';

function parseReleaseId(search: string): number | null {
  // Reject null, empty, whitespace-only, and non-integer. `Number()`
  // (strict) ensures `123-abc` is NaN rather than 123.
  const raw = new URLSearchParams(search).get('release')?.trim();
  if (!raw) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

function parseVariantOverride(search: string): LookupVariant | null {
  const params = new URLSearchParams(search);
  const v = params.get('variant');
  if (v === 'search-first' || v === 'recents-first' || v === 'hybrid') return v;
  return null;
}

function renderWithRecents(
  variant: LookupVariant,
  recents: RecentPlay[],
): JSX.Element {
  if (variant === 'recents-first') return <LookupViewRecentsFirst recents={recents} />;
  if (variant === 'hybrid') return <LookupViewHybrid recents={recents} />;
  return <LookupViewSearchFirst recents={recents} />;
}

function renderUnscoped(
  variant: LookupVariant,
  recents: ReturnType<typeof useRecentPlays>['recents'],
  variantOverride: LookupVariant | null,
): JSX.Element {
  // Loading: hold a neutral screen until recents resolves so a
  // user with history doesn't see search-first flash before the
  // hero variant lands. URL `?variant=` override skips the gate
  // (engineering escape hatch).
  if (recents === null) {
    if (!variantOverride) return <Centered>Loading…</Centered>;
    return <LookupViewSearchFirst recents={null} />;
  }
  if (recents.length === 0) return <LookupViewSearchFirst recents={recents} />;
  return renderWithRecents(variant, recents);
}

/**
 * Tracks dismiss telemetry for LookupView. Fires `identify_lookup_dismiss`
 * on unmount when the user didn't pick anything (pickedRef.current === false).
 * Returns `pickedRef` for consumers to set on successful pick.
 */
function useLookupDismissTelemetry(variant: LookupVariant) {
  const mountedAtRef = useRef(Date.now());
  const variantAtMountRef = useRef(variant);
  const pickedRef = useRef(false);
  useEffect(() => {
    variantAtMountRef.current = variant;
  }, [variant]);
  useEffect(() => {
    return () => {
      if (pickedRef.current) return;
      telemetryTrack('identify_lookup_dismiss', {
        variant: variantAtMountRef.current,
        ms_in_view: Date.now() - mountedAtRef.current,
      });
    };
  }, []);
  return pickedRef;
}

/**
 * LookupView orchestrator. Routes `?release=<rid>` to the scoped
 * fast path (D-4 behavior preserved) and otherwise picks one of
 * three sibling variants based on `useRecentPlays` data:
 * `search-first` (default during loading + empty recents),
 * `recents-first` (≥5), `hybrid` (1-4).
 *
 * `?variant=<name>` URL param overrides the picker (engineering).
 *
 * Telemetry: fires `identify_lookup_dismiss` on unmount/navigate-
 * away with `{variant, ms_in_view}`.
 *
 * Spec: docs/features/identify-lookup-search-rebuild/plan.md.
 * Extended: docs/features/identify-lookup-recents-one-tap/plan.md.
 */
export function LookupView() {
  const search = useSearch();
  const [, navigate] = useLocation();
  const payload = useStore((s) => s.payload);
  const { recents } = useRecentPlays(20);

  const urlReleaseId = parseReleaseId(search);
  const variantOverride = parseVariantOverride(search);
  const fromNeedsId = parseFromNeedsId(search);

  // When the URL has a ?release=<id>, fetch its tracklist from the API.
  // Needed for past-album recents taps where the WS payload carries a
  // different (currently-playing) album's tracks.
  const { tracks: apiTracks } = useReleaseTracklist(urlReleaseId);

  // Stable variant: once data settles, lock the picked variant
  // so a re-render with new recents doesn't cause a layout jump.
  // URL override always wins.
  const [stableVariant, setStableVariant] = useState<LookupVariant>('search-first');
  useEffect(() => {
    if (recents !== null) setStableVariant(pickLookupVariant(recents));
  }, [recents]);
  const variant: LookupVariant = variantOverride ?? stableVariant;

  const pickedRef = useLookupDismissTelemetry(variant);

  return (
    <PickedProvider value={pickedRef}>
      <LookupBody
        urlReleaseId={urlReleaseId}
        payload={payload}
        variant={variant}
        variantOverride={variantOverride}
        recents={recents}
        apiTracks={apiTracks}
        fromNeedsId={fromNeedsId}
        onScopedDone={() => navigate('/')}
      />
    </PickedProvider>
  );
}

interface LookupBodyProps {
  urlReleaseId: number | null;
  payload: ReturnType<typeof useStore.getState>['payload'];
  variant: LookupVariant;
  variantOverride: LookupVariant | null;
  recents: ReturnType<typeof useRecentPlays>['recents'];
  /**
   * API-fetched tracklist for `urlReleaseId`. `null` while loading or
   * when `urlReleaseId` is null. Used for past-album scoped views where
   * the WS payload carries a different album's tracks.
   */
  apiTracks: TracklistItem[] | null;
  /** When true, the user navigated here via "Help identify this song"
   *  on the NEEDS_ID screen. The scoped view suppresses current/guess
   *  highlights so the cascade's stale guess can't bias a one-tap pin
   *  to the wrong track. See docs/features/recents-one-tap-silent-pin/. */
  fromNeedsId: boolean;
  onScopedDone: () => void;
}

function LookupBody({
  urlReleaseId, payload, variant, variantOverride, recents, apiTracks, fromNeedsId, onScopedDone,
}: LookupBodyProps) {
  if (urlReleaseId !== null) {
    return (
      <LookupViewScoped
        {...pickScopedProps(urlReleaseId, payload, apiTracks, fromNeedsId)}
        onDone={onScopedDone}
      />
    );
  }
  if (!payload) return <Centered>Connecting…</Centered>;
  return renderUnscoped(variant, recents, variantOverride);
}
