import { useEffect, useRef } from 'react';

/**
 * Calls `callback` after the user presses and holds anywhere on the window
 * for `ms` milliseconds. Any pointermove cancels (so drag/scroll doesn't fire).
 */
export function useLongPress(callback: () => void, ms = 1000) {
  const timer = useRef<number | null>(null);
  useEffect(() => {
    const start = () => {
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(callback, ms);
    };
    const cancel = () => {
      if (timer.current) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
    };
    window.addEventListener('pointerdown', start);
    window.addEventListener('pointerup', cancel);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('pointermove', cancel);
    return () => {
      window.removeEventListener('pointerdown', start);
      window.removeEventListener('pointerup', cancel);
      window.removeEventListener('pointercancel', cancel);
      window.removeEventListener('pointermove', cancel);
      cancel();
    };
  }, [callback, ms]);
}
