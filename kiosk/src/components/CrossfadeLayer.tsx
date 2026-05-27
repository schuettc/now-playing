import { motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { appendAttemptSuffix } from './crossfadeUrl';

// Retry schedule for transient /art/<id> 404s. The orchestrator's MusicBrainz
// lookup is async and can land the file on disk a few seconds after the WS
// payload first announces the release. Without a nudge the browser holds the
// failed-load state for the rest of the song.
const RETRY_DELAYS_MS = [5000, 15000, 30000];

interface Props {
  src: string;
  alt: string;
  isReady: boolean;
  onReady: () => void;
}

export function CrossfadeLayer({ src, alt, isReady, onReady }: Props) {
  const [attempt, setAttempt] = useState(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
    };
  }, []);

  const handleError = () => {
    if (attempt >= RETRY_DELAYS_MS.length) return;
    const delay = RETRY_DELAYS_MS[attempt];
    if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current);
    retryTimerRef.current = setTimeout(() => {
      retryTimerRef.current = null;
      setAttempt((a) => a + 1);
    }, delay);
  };

  return (
    <motion.img
      key={attempt}
      src={appendAttemptSuffix(src, attempt)}
      alt={alt}
      draggable={false}
      initial={{ opacity: 0 }}
      animate={{ opacity: isReady ? 1 : 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      onLoad={onReady}
      onError={handleError}
      className="absolute inset-0 h-full w-full object-cover"
      style={{ willChange: 'opacity' }}
    />
  );
}
