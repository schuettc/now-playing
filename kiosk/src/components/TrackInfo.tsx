import { motion } from 'framer-motion';
import type { NowPlaying } from '@/types';
import { getSidePrefix } from './trackInfoHelpers';

interface Props {
  data: NowPlaying;
  identity: string;
}

// Close text-shadow for letter-edge legibility without the visible
// dark halo the prior 0_2px_24px_0.6 blur produced against bright
// album tints (e.g. The Decemberists' yellow backdrop).
const TITLE_BASE = 'text-6xl font-semibold leading-tight tracking-tight [text-shadow:0_1px_3px_rgba(0,0,0,0.45)]';

/** Tailwind class for the track title, reflecting predicted/confirmed state. */
function buildTitleClass(predicted: boolean | undefined): string {
  return `${TITLE_BASE} ${predicted ? 'italic text-white/85' : 'text-white'}`;
}

function AlbumYearLine({ album, year }: { album: string | undefined; year: string | number | undefined }) {
  if (!album) return <div className="min-h-[1.75rem] text-xl text-white/60" />;
  return (
    <div className="min-h-[1.75rem] text-xl text-white/60">
      {album}
      {year ? <span className="text-white/40"> · {year}</span> : null}
    </div>
  );
}

export function TrackInfo({ data, identity }: Props) {
  const { title, artist, album, year, predicted } = data;
  const sidePrefix = getSidePrefix(data);

  return (
    <motion.div
      key={identity}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="relative flex w-full max-w-[42rem] flex-col gap-2.5 text-left text-white"
    >
      {/* Vinyl side prefix is an annotation in the column's headroom
          above the title — absolute-positioned so its presence/absence
          doesn't change the title's Y. The title's top edge anchors
          on the shoulder line either way, which is what visually
          aligns it with the right column's first track row. */}
      {sidePrefix && (
        <div
          className="absolute bottom-full left-0 mb-3 whitespace-nowrap font-mono text-[13px] uppercase tracking-[0.32em] text-white/50"
          aria-label={`Side ${sidePrefix}`}
        >
          Side {sidePrefix}
        </div>
      )}
      <h1 className={buildTitleClass(predicted)}>
        {title ?? <span className="text-white/40">Unknown Track</span>}
      </h1>
      <div className="text-3xl font-medium text-white/85">
        {artist ?? <span className="text-white/40">Unknown Artist</span>}
      </div>
      <AlbumYearLine album={album} year={year} />
    </motion.div>
  );
}
