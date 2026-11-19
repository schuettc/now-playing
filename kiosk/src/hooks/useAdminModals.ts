import { useCallback, useEffect, useState } from 'react';
import type { ScreenState } from '@/hooks/useScreenState';
import { useLongPress } from '@/hooks/useLongPress';

/** Changes exactly when open modals must close: a new track, or going idle. */
export function modalResetKey(trackId: string, screenKind: ScreenState['kind']): string {
  return screenKind === 'idle' ? 'idle' : `track:${trackId}`;
}

/**
 * Owns the open/close state for the three top-bar modals:
 *   - `pickerOpen` — the SomethingWrongPicker bottom sheet
 *   - `artPickerOpen` — the ArtPicker, opened from the picker's
 *     "Change album art" row
 *   - `alternatesOpen` — the AlternatesModal, opened from the
 *     picker's "Wrong album" row
 *
 * Also wires the long-press gesture (1s anywhere on screen) as a
 * fallback opener for the picker. All three close when the track
 * changes or the screen goes idle, so a modal left open never acts
 * on, or reappears over, something it was not opened for.
 */
export function useAdminModals(
  adminAvailable: boolean,
  trackId: string,
  screenKind: ScreenState['kind'],
) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [artPickerOpen, setArtPickerOpen] = useState(false);
  const [alternatesOpen, setAlternatesOpen] = useState(false);

  const resetKey = modalResetKey(trackId, screenKind);
  useEffect(() => {
    setPickerOpen(false);
    setArtPickerOpen(false);
    setAlternatesOpen(false);
  }, [resetKey]);

  useLongPress(
    useCallback(() => {
      if (adminAvailable) setPickerOpen(true);
    }, [adminAvailable]),
    1000,
  );

  const openPicker = useCallback(() => setPickerOpen(true), []);
  const closePicker = useCallback(() => setPickerOpen(false), []);
  const closeArtPicker = useCallback(() => setArtPickerOpen(false), []);
  const closeAlternates = useCallback(() => setAlternatesOpen(false), []);
  const changeArt = useCallback(() => {
    setPickerOpen(false);
    setArtPickerOpen(true);
  }, []);
  const openAlternates = useCallback(() => {
    setPickerOpen(false);
    setAlternatesOpen(true);
  }, []);

  return {
    pickerOpen,
    artPickerOpen,
    alternatesOpen,
    openPicker,
    closePicker,
    closeArtPicker,
    closeAlternates,
    changeArt,
    openAlternates,
  };
}
