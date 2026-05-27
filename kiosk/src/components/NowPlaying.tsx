import { NowPlayingView } from '@/components/now-playing/NowPlayingView';
import { usePrepareNowPlayingProps } from '@/hooks/usePrepareNowPlayingProps';

/**
 * Top-level kiosk view. Subscribes to the WebSocket-backed store
 * (via `usePrepareNowPlayingProps`) and renders `NowPlayingView`.
 *
 * IdentifyProvider lives at App.tsx now (D-4 lift) so the pin
 * lifecycle survives route transitions between `/`, `/lookup`,
 * and `/identify`.
 */
export function NowPlaying() {
  const props = usePrepareNowPlayingProps();
  return <NowPlayingView {...props} />;
}
