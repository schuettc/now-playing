import { AnimatePresence, motion } from 'framer-motion';

interface Props {
  toast: { msg: string; error?: boolean } | null;
}

/**
 * Single-slot toast notification used by /identify for "Set as
 * playing · A1" success and "Search failed: …" errors. Error toasts
 * render in red; success toasts in the kiosk's accent blue.
 */
export function Toast({ toast }: Props) {
  return (
    <AnimatePresence>
      {toast && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
          transition={{ duration: 0.2 }}
          className={`fixed left-1/2 bottom-24 z-20 -translate-x-1/2 rounded-full px-5 py-3 text-[15px] text-white shadow-lg ${
            toast.error ? 'bg-[#ff7466]' : 'bg-[#6e8aff]'
          }`}
        >
          {toast.msg}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
