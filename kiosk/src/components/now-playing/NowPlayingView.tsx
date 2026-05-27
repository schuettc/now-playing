import { ScreenOverlay } from '@/components/now-playing/ScreenOverlay';
import { TrackLayout } from '@/components/now-playing/TrackLayout';
import { StatusOverlay } from '@/components/now-playing/StatusOverlay';
import { CornerLinks } from '@/components/now-playing/CornerLinks';
import { ModalStack } from '@/components/now-playing/ModalStack';
import { ConnectionDot } from '@/components/now-playing/ConnectionDot';
import { TrackBackdrop } from '@/components/now-playing/TrackBackdrop';
import { LearningChip } from '@/components/feedback/LearningChip';
import { InlineError } from '@/components/feedback/InlineError';
import { isVinylAwaitingConfirm } from '@/components/now-playing/vinylGuards';
import { isVinylNeedsId } from '@/hooks/useScreenState';
import type { ScreenState } from '@/hooks/useScreenState';
import type { Guess, IdentifyState, NowPlaying as NowPlayingData } from '@/types';

interface Props {
  data: NowPlayingData | null;
  connected: boolean;
  screen: ScreenState;
  identifyState: IdentifyState;
  adminAvailable: boolean;
  artId: string;
  trackId: string;
  effectiveArtUrl: string | undefined;
  artCacheBust: number;
  isTrack: boolean;
  isPaused: boolean;
  pickerOpen: boolean;
  artPickerOpen: boolean;
  alternatesOpen: boolean;
  onOpenPicker: () => void;
  onClosePicker: () => void;
  onCloseArtPicker: () => void;
  onCloseAlternates: () => void;
  onChangeArt: () => void;
  onOpenAlternates: () => void;
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
}

/** Returns the active guess when the kiosk awaits user confirmation, else null. */
function activeGuessFor(identifyState: IdentifyState, data: NowPlayingData | null): Guess | null {
  if (data === null) return null;
  if (!isVinylAwaitingConfirm(identifyState, data.source)) return null;
  return data.guess ?? null;
}

// The track surface (backdrop + grid) renders for `track` and for
// non-vinyl `needs-id` states. Non-vinyl `needs-id` (AirPlay/streaming)
// preserves the last-known payload's metadata behind the StatusPill +
// "Something wrong?" affordance. Vinyl `needs-id` is routed to
// VinylIdentifying in ScreenOverlay instead — rendering TrackLayout
// with a null-title vinyl payload would produce the "Unknown Track /
// Unknown Artist / NO ART" failure UI.
function TrackSurface(p: Props) {
  if (p.screen.kind !== 'track' && p.screen.kind !== 'needs-id') return null;
  if (isVinylNeedsId(p.screen)) return null;
  return (
    <TrackLayout
      data={p.screen.data}
      artId={p.artId}
      trackId={p.trackId}
      effectiveArtUrl={p.effectiveArtUrl}
      artCacheBust={p.artCacheBust}
      isPaused={p.isPaused}
      guess={activeGuessFor(p.identifyState, p.data)}
    />
  );
}

// Top-right cluster: status pill (source + identify confidence) +
// labeled "Something wrong?" button. The button replaces the old `···`
// AdminOverlay opener AND the standalone UndoStrip — both retired in
// favor of a single picker (something-wrong-picker).
function TopRightCluster(p: Props) {
  if (!p.data || p.screen.kind === 'idle') return null;
  return (
    <StatusOverlay
      data={p.data}
      identifyState={p.identifyState}
      adminAvailable={p.adminAvailable}
      onOpenPicker={p.onOpenPicker}
    />
  );
}

export function NowPlayingView(p: Props) {
  return (
    <div className="relative h-screen w-screen overflow-hidden bg-black">
      <TrackBackdrop
        show={p.isTrack || (p.screen.kind === 'needs-id' && !isVinylNeedsId(p.screen))}
        artId={p.artId}
        artCacheBust={p.artCacheBust}
        effectiveArtUrl={p.effectiveArtUrl}
        isPaused={p.isPaused}
      />
      <ScreenOverlay state={p.screen} identifyState={p.identifyState} />
      <TrackSurface {...p} />
      <TopRightCluster {...p} />
      <LearningChip />
      <InlineError />
      <ConnectionDot connected={p.connected} />
      <CornerLinks
        data={p.data}
        showTrack={p.isTrack}
        showNeedsId={p.screen.kind === 'needs-id'}
        showVinylIdentifying={p.screen.kind === 'vinyl-identifying'}
      />
      <ModalStack
        data={p.data}
        identifyState={p.identifyState}
        pickerOpen={p.pickerOpen}
        artPickerOpen={p.artPickerOpen}
        alternatesOpen={p.alternatesOpen}
        onClosePicker={p.onClosePicker}
        onCloseArtPicker={p.onCloseArtPicker}
        onCloseAlternates={p.onCloseAlternates}
        onChangeArt={p.onChangeArt}
        onOpenAlternates={p.onOpenAlternates}
        onSaved={p.onSaved}
      />
    </div>
  );
}
