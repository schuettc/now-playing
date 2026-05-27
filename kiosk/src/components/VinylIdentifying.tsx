import { motion } from 'framer-motion';
import type { IdentifyState } from '@/types';

interface Props {
  /** Controls whether to show the softer "Listening…" state (still trying)
   *  or the full "Identifying record · Help identify" affordance. */
  identifyState: IdentifyState;
}

export function VinylIdentifying({ identifyState }: Props) {
  const isIdentifying = identifyState === 'identifying';
  return (
    <motion.div
      key="vinyl-identifying"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.6 }}
      className="absolute inset-0 flex flex-col items-center justify-center gap-10 bg-gradient-to-b from-zinc-950 via-black to-zinc-950"
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.5em] text-amber-300/70">
        Vinyl
      </div>

      <div className="relative h-72 w-72">
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ duration: 6, ease: 'linear', repeat: Infinity }}
          className="absolute inset-0 rounded-full bg-zinc-900"
          style={{
            backgroundImage:
              'repeating-radial-gradient(circle at center, rgba(255,255,255,0.04) 0 1px, transparent 1px 4px)',
          }}
        />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="h-24 w-24 rounded-full bg-amber-700/80 ring-4 ring-amber-900/60" />
        </div>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="h-3 w-3 rounded-full bg-zinc-950" />
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span className="text-2xl font-light tracking-wide text-white/85">
          {isIdentifying ? 'Listening' : 'Identifying record'}
        </span>
        <DotPulse />
      </div>
      <div className="max-w-md text-center text-sm text-white/40">
        {isIdentifying
          ? 'Matching against your collection…'
          : 'Listening to the line-in and matching against your collection.'}
      </div>

      <a
        href="/identify?from=needs-id"
        className="rounded-full bg-white/95 px-8 py-3 text-base font-medium text-black hover:bg-white"
      >
        Help identify this song
      </a>
    </motion.div>
  );
}

function DotPulse() {
  return (
    <div className="flex items-center gap-1.5">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-white/70"
          animate={{ opacity: [0.2, 1, 0.2] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.18 }}
        />
      ))}
    </div>
  );
}
