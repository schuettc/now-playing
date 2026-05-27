import { AnimatePresence } from 'framer-motion';
import { showsVinylOverlay } from '@/hooks/useScreenState';
import type { ScreenState } from '@/hooks/useScreenState';
import type { IdentifyState } from '@/types';
import { IdleScreen } from '@/components/IdleScreen';
import { AirPlayScreen } from '@/components/AirPlayScreen';
import { VinylIdentifying } from '@/components/VinylIdentifying';

interface Props {
  state: ScreenState;
  identifyState: IdentifyState;
}

/**
 * `AnimatePresence mode="wait"` switch for the mutually-exclusive
 * screen overlays — idle / airplay / vinyl-identifying. The track
 * state is rendered separately by `TrackLayout` because it overlaps
 * with the blurred backdrop.
 *
 * `needs-id` + vinyl: the orchestrator escalated after N unmatched
 * heartbeats — render `VinylIdentifying` (the same full-screen
 * spinner used during the active-identifying phase). This prevents
 * the "Unknown Track / Unknown Artist / NO ART" fallback that appeared
 * when `TrackLayout` rendered with a null-title payload.
 *
 * `needs-id` + non-vinyl (AirPlay/streaming): the last-known metadata
 * is usually valid, so the regular `TrackSurface` path in `NowPlayingView`
 * handles it with the StatusPill(needs-id) + "Something wrong?" overlay.
 *
 * `identifyState` is forwarded to `VinylIdentifying` to control
 * whether the "Help identify this song" affordance is shown or suppressed
 * (during the `identifying` transient state — we haven't given up yet).
 */
export function ScreenOverlay({ state, identifyState }: Props) {
  return (
    <AnimatePresence mode="wait">
      {state.kind === 'idle' && <IdleScreen key="idle" />}
      {state.kind === 'airplay' && (
        <AirPlayScreen key="airplay" data={state.data} />
      )}
      {showsVinylOverlay(state) && (
        <VinylIdentifying key="vinyl-identifying" identifyState={identifyState} />
      )}
    </AnimatePresence>
  );
}
