import type { IdentifyState, NowPlaying } from '@/types';
import { StatusPill } from '@/components/StatusPill';
import { SideTimer } from '@/components/SideTimer';

interface Props {
  data: NowPlaying;
  identifyState: IdentifyState;
  adminAvailable: boolean;
  onOpenPicker: () => void;
}

/**
 * Top-right corner cluster: a single unified pill (status + tap-target
 * for the SomethingWrongPicker when adminAvailable) and the SideTimer
 * for vinyl. The pill replaces the prior pill-plus-separate-button
 * layout — one shape, one tap target.
 */
export function StatusOverlay({
  data,
  identifyState,
  adminAvailable,
  onOpenPicker,
}: Props) {
  return (
    <div className="absolute right-10 top-8 z-20 flex flex-col items-end gap-3">
      <StatusPill
        source={data.source}
        identifyState={identifyState}
        onTap={adminAvailable ? onOpenPicker : undefined}
      />
      {data.source === 'vinyl' && <SideTimer data={data} />}
    </div>
  );
}
