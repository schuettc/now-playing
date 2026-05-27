import { motion } from 'framer-motion';
import type { ReactNode } from 'react';

interface Props {
  onClose: () => void;
  children: ReactNode;
}

export function ArtPickerShell({ onClose, children }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/85 backdrop-blur-sm"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.96, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.96, opacity: 0 }}
        transition={{ duration: 0.2 }}
        onClick={(e) => e.stopPropagation()}
        className="m-8 flex max-h-[88vh] w-full max-w-5xl flex-col gap-5 overflow-hidden rounded-2xl bg-zinc-900/95 p-8 ring-1 ring-white/10"
      >
        {children}
      </motion.div>
    </motion.div>
  );
}
