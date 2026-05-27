import { motion } from 'framer-motion';

/**
 * Small "paused" overlay rendered on top of the album art.
 *
 * Two CSS-styled bars rather than a Lucide icon — keeps us off
 * an extra dependency and matches the project's icon-less
 * aesthetic (matches StatusPill's chip-style ring).
 */
export function PauseIndicator() {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      aria-label="Paused"
      className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center"
    >
      {/* Bars only — no backdrop circle. drop-shadow gives them legibility
          on any cover (light or dark) without putting a visible disc in
          the middle of the art. */}
      <div
        className="flex items-center justify-center gap-7"
        style={{ filter: 'drop-shadow(0 4px 18px rgba(0,0,0,0.65))' }}
      >
        <span className="block h-44 w-9 rounded-md bg-white/90" />
        <span className="block h-44 w-9 rounded-md bg-white/90" />
      </div>
    </motion.div>
  );
}
