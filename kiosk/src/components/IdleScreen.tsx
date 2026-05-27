import { motion } from 'framer-motion';
import { useClock } from '@/hooks/useClock';

export function IdleScreen() {
  const now = useClock(1000);

  const time = now.toLocaleTimeString([], {
    hour: 'numeric',
    minute: '2-digit',
  });
  const date = now.toLocaleDateString([], {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });

  return (
    <motion.div
      key="idle"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.6 }}
      className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-black"
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.5em] text-white/30">
        Now Playing · Idle
      </div>
      <div className="text-[12rem] font-light leading-none tracking-tight text-white/85">
        {time}
      </div>
      <div className="text-xl text-white/45">{date}</div>
    </motion.div>
  );
}
