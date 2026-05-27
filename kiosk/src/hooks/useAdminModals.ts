import { useCallback, useState } from 'react';
import { useLongPress } from '@/hooks/useLongPress';

/**
 * Owns the open/close state for the three top-bar modals:
 *   - `pickerOpen` — the SomethingWrongPicker bottom sheet
 *   - `artPickerOpen` — the ArtPicker, opened from the picker's
 *     "Change album art" row
 *   - `alternatesOpen` — the AlternatesModal, opened from the
 *     picker's "Wrong album" row
 *
 * Also wires the long-press gesture (1s anywhere on screen) as a
 * fallback opener for the picker.
 */
export function useAdminModals(adminAvailable: boolean) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [artPickerOpen, setArtPickerOpen] = useState(false);
  const [alternatesOpen, setAlternatesOpen] = useState(false);

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
