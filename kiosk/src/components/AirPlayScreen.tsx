import { motion } from 'framer-motion';
import type { NowPlaying } from '@/types';

interface Props {
  data: NowPlaying;
}

export function AirPlayScreen({ data }: Props) {
  return (
    <motion.div
      key="airplay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.6 }}
      className="absolute inset-0 flex flex-col items-center justify-center gap-6 bg-gradient-to-b from-sky-950 via-black to-black"
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.5em] text-sky-300/70">
        AirPlay
      </div>
      <div className="text-5xl font-light tracking-tight text-white/90">
        Streaming from
      </div>
      <div className="text-3xl font-medium text-white">
        {data.device_name ?? 'Unknown Device'}
      </div>
      <div className="mt-4 max-w-md text-center text-sm text-white/40">
        AirPlay sources don't expose track metadata to Sonos.
      </div>
    </motion.div>
  );
}
