import { computeAdminAvailable } from '@/components/now-playing/adminAvailable';
import { useAdminModals } from '@/hooks/useAdminModals';
import { useArtOverride } from '@/hooks/useArtOverride';
import { useArtPrefetch } from '@/hooks/useArtPrefetch';
import { useIdentifyContext } from '@/hooks/identifyContext';
import { deriveScreenState, type ScreenState } from '@/hooks/useScreenState';
import { artIdentityOf, trackIdentityOf } from '@/lib/art';
import { useStore } from '@/store/useStore';
import type { NowPlaying } from '@/types';

function identitiesOf(data: NowPlaying | null) {
  if (!data) return { artId: 'idle', trackId: 'idle' };
  return { artId: artIdentityOf(data), trackId: trackIdentityOf(data) };
}

function trackFlags(screen: ScreenState) {
  if (screen.kind !== 'track') return { isTrack: false, isPaused: false };
  return { isTrack: true, isPaused: screen.isPaused };
}

/**
 * Aggregates every store read, derivation, and feature hook needed by
 * `NowPlayingView`. Extracted from `NowPlaying.tsx` to keep the
 * top-level component a thin orchestrator (one render, no inline
 * branching) and to scope the data-prep complexity in one place.
 */
export function usePrepareNowPlayingProps() {
  const data = useStore((s) => s.payload);
  const connected = useStore((s) => s.connected);

  const screen = deriveScreenState(data);
  // useIdentifyState now lives in App.tsx (lifted in D-4 so the pin
  // lifecycle survives route transitions). We consume the same
  // instance via context here for `identifyState`. Deep-tree
  // surfaces (GuessConfirm, TappableTrackRow) consume the same
  // context directly.
  const identify = useIdentifyContext();
  useArtPrefetch(data);

  const adminAvailable = computeAdminAvailable(data);
  const modals = useAdminModals(adminAvailable);

  const { artId, trackId } = identitiesOf(data);
  const { effectiveArtUrl, artCacheBust, onSaved } = useArtOverride({
    trackId,
    artUrl: data?.art_url,
  });
  const { isTrack, isPaused } = trackFlags(screen);

  return {
    data,
    connected,
    screen,
    identifyState: identify.identifyState,
    adminAvailable,
    artId,
    trackId,
    effectiveArtUrl,
    artCacheBust,
    isTrack,
    isPaused,
    pickerOpen: modals.pickerOpen,
    artPickerOpen: modals.artPickerOpen,
    alternatesOpen: modals.alternatesOpen,
    onOpenPicker: modals.openPicker,
    onClosePicker: modals.closePicker,
    onCloseArtPicker: modals.closeArtPicker,
    onCloseAlternates: modals.closeAlternates,
    onChangeArt: modals.changeArt,
    onOpenAlternates: modals.openAlternates,
    onSaved,
  };
}
