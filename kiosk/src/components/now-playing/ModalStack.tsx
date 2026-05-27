import { AnimatePresence } from 'framer-motion';
import { SomethingWrongPicker } from '@/components/SomethingWrongPicker';
import { ArtPicker } from '@/components/ArtPicker';
import { AlternatesModal } from '@/components/AlternatesModal';
import { useStore } from '@/store/useStore';
import type { IdentifyState, NowPlaying } from '@/types';

interface ModalStackProps {
  data: NowPlaying | null;
  identifyState: IdentifyState;
  pickerOpen: boolean;
  artPickerOpen: boolean;
  alternatesOpen: boolean;
  onClosePicker: () => void;
  onCloseArtPicker: () => void;
  onCloseAlternates: () => void;
  onChangeArt: () => void;
  onOpenAlternates: () => void;
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
}

function PickerSlot({
  data,
  identifyState,
  open,
  onClose,
  onChangeArt,
  onOpenAlternates,
}: {
  data: NowPlaying | null;
  identifyState: IdentifyState;
  open: boolean;
  onClose: () => void;
  onChangeArt: () => void;
  onOpenAlternates: () => void;
}) {
  return (
    <AnimatePresence>
      {open && data && (
        <SomethingWrongPicker
          data={data}
          identifyState={identifyState}
          onClose={onClose}
          onChangeArt={onChangeArt}
          onOpenAlternates={onOpenAlternates}
        />
      )}
    </AnimatePresence>
  );
}

function ArtPickerSlot({
  data,
  open,
  onClose,
  onSaved,
}: {
  data: NowPlaying | null;
  open: boolean;
  onClose: () => void;
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
}) {
  return (
    <AnimatePresence>
      {open && data && (
        <ArtPicker data={data} onClose={onClose} onSaved={onSaved} />
      )}
    </AnimatePresence>
  );
}

type SelectReleaseResult = { ok: true } | { ok: false; reason: string };

async function postSelectRelease(
  release_id: number,
  track_position?: string,
  track_title?: string,
): Promise<SelectReleaseResult> {
  try {
    const res = await fetch('/control/select-release', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id, track_position, track_title }),
    });
    if (res.ok) return { ok: true };
    return { ok: false, reason: 'select-release-failed' };
  } catch {
    return { ok: false, reason: 'network' };
  }
}

function useAlternateSelectHandler(onClose: () => void) {
  const setPinErrorReason = useStore((s) => s.setPinErrorReason);
  return async (
    release_id: number,
    track_position?: string,
    track_title?: string,
  ) => {
    const result = await postSelectRelease(release_id, track_position, track_title);
    if (result.ok) onClose();
    else setPinErrorReason(result.reason);
  };
}

// Why: this slot's branches (open ∧ data ∧ alternates length > 0) are
// intrinsic to its job — gate visibility, render the modal, route the
// select handler. Extracting further produces an interface tax larger
// than the saving. Slot is also small (≤30 LOC) and pattern-matched
// across the three slots in this file.
// fallow-ignore-next-line complexity
function AlternatesSlot({
  data,
  open,
  onClose,
}: {
  data: NowPlaying | null;
  open: boolean;
  onClose: () => void;
}) {
  const onSelect = useAlternateSelectHandler(onClose);
  const alternates = data?.alternate_releases ?? [];
  const visible = open && data !== null && alternates.length > 0;
  return (
    <AnimatePresence>
      {visible && data && (
        <AlternatesModal
          data={data}
          alternates={alternates}
          onClose={onClose}
          onSelect={onSelect}
        />
      )}
    </AnimatePresence>
  );
}

/**
 * AnimatePresence wrappers for the three top-bar modals: the
 * SomethingWrongPicker bottom sheet, the ArtPicker, and the
 * AlternatesModal. Open state lives in `usePrepareNowPlayingProps`
 * (via `useAdminModals`); this component owns presentation +
 * the `/control/select-release` call for alternates.
 */
export function ModalStack({
  data,
  identifyState,
  pickerOpen,
  artPickerOpen,
  alternatesOpen,
  onClosePicker,
  onCloseArtPicker,
  onCloseAlternates,
  onChangeArt,
  onOpenAlternates,
  onSaved,
}: ModalStackProps) {
  return (
    <>
      <PickerSlot
        data={data}
        identifyState={identifyState}
        open={pickerOpen}
        onClose={onClosePicker}
        onChangeArt={onChangeArt}
        onOpenAlternates={onOpenAlternates}
      />
      <ArtPickerSlot
        data={data}
        open={artPickerOpen}
        onClose={onCloseArtPicker}
        onSaved={onSaved}
      />
      <AlternatesSlot
        data={data}
        open={alternatesOpen}
        onClose={onCloseAlternates}
      />
    </>
  );
}
