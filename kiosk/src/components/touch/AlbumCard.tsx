import { motion } from 'framer-motion';
import { MOTION } from '@/lib/motion';

type AlbumCardSize = 'sm' | 'md' | 'lg';

interface Props {
  art?: string;
  title: string;
  subtitle?: string;
  size?: AlbumCardSize;
  onClick: () => void;
}

const SIZE_PX: Record<AlbumCardSize, number> = {
  sm: 140,
  md: 180,
  lg: 220,
};

export function albumCardSize(size: AlbumCardSize): number {
  return SIZE_PX[size];
}

/**
 * Touch-friendly album-art card primitive — square art over a
 * two-line caption. Hover lift (`-2px`) + press shrink
 * (`scale 0.99`) via Framer Motion.
 *
 * Distinct from `features/identify/AlbumCard.tsx` (the legacy
 * search-result row with inline tracklist expansion). The new
 * primitive lives in `components/touch/` and is consumed by the
 * D-5 LookupView variants for the "recents" hero rows.
 *
 * Spec: docs/features/confirmed-fingerprint-coverage/design-output/
 * README.md § "Component vocabulary" → AlbumCard.
 */
export function AlbumCard({ art, title, subtitle, size = 'md', onClick }: Props) {
  const px = SIZE_PX[size];
  return (
    <motion.button
      type="button"
      onClick={onClick}
      whileHover={{ y: -2 }}
      whileTap={{ y: 2, scale: 0.99 }}
      transition={{ duration: MOTION.cardLift, ease: 'easeOut' }}
      data-testid="album-card"
      className="flex flex-col items-start gap-2 bg-transparent text-left"
      style={{ width: px }}
    >
      <div
        className="overflow-hidden rounded-[4px]"
        style={{
          width: px,
          height: px,
          background: 'var(--text-hairline)',
        }}
      >
        {art && (
          <img
            src={art}
            alt=""
            className="h-full w-full object-cover"
            loading="lazy"
          />
        )}
      </div>
      <div className="w-full">
        <div
          className="truncate text-[16px] font-medium"
          style={{ color: 'var(--text-primary)' }}
        >
          {title}
        </div>
        {subtitle && (
          <div
            className="truncate text-[13px]"
            style={{ color: 'var(--text-secondary)' }}
          >
            {subtitle}
          </div>
        )}
      </div>
    </motion.button>
  );
}
