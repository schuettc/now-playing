import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Toast auto-dismiss window. 2400ms is the empirical floor at which a
 * short message ("Set as playing · A1") still reads comfortably on a
 * touch kiosk without overlapping the next user action.
 */
const TOAST_MS = 2400;

interface ToastState {
  msg: string;
  error?: boolean;
}

/**
 * Self-dismissing toast notification state. Owns the visible toast
 * value, a `showToast` callback that auto-clears after `TOAST_MS`, and
 * the unmount cleanup so a pending timer can't fire setState against a
 * stale instance.
 */
export function useToast(): {
  toast: ToastState | null;
  showToast: (msg: string, error?: boolean) => void;
} {
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastTimer = useRef<number | null>(null);

  const showToast = useCallback((msg: string, error?: boolean) => {
    setToast({ msg, error });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), TOAST_MS);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
    };
  }, []);

  return { toast, showToast };
}
