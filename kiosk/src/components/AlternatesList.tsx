import { useState } from 'react';
import type { AlternateRelease } from '@/types';

interface Props {
  alternates: AlternateRelease[];
  onSelect: (
    release_id: number,
    track_position?: string,
    track_title?: string,
  ) => void;
}

/** Builds the meta sub-line (position · year · format) skipping empties. */
export function alternateMetaLine(alt: AlternateRelease): string {
  const parts: string[] = [];
  if (alt.track_position) parts.push(alt.track_position);
  if (alt.year !== undefined) parts.push(String(alt.year));
  if (alt.format) parts.push(alt.format);
  return parts.join(' · ');
}

/**
 * Row for one alternate-pressing candidate. Renders the cover at
 * /art/<release_id> with a placeholder while the image loads or when
 * the cache miss falls back to nothing. The whole row is the touch
 * target; covers are decorative and don't need their own click.
 */
function AlternateCover({ releaseId }: { releaseId: number }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const shown = loaded && !failed;
  return (
    <div className="h-16 w-16 shrink-0 overflow-hidden rounded-md bg-white/5 ring-1 ring-white/10">
      {!failed && (
        <img
          src={`/art/${releaseId}`}
          alt=""
          aria-hidden="true"
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          className={`h-full w-full object-cover transition-opacity duration-200 ${shown ? 'opacity-100' : 'opacity-0'}`}
        />
      )}
    </div>
  );
}

function AlternateRow({
  alt,
  onSelect,
}: {
  alt: AlternateRelease;
  onSelect: Props['onSelect'];
}) {
  const meta = alternateMetaLine(alt);
  return (
    <button
      onClick={() => onSelect(alt.release_id, alt.track_position, alt.track_title)}
      className="flex min-h-[88px] items-center gap-4 rounded-lg bg-white/5 p-3 text-left text-white ring-1 ring-white/10 transition hover:bg-white/10"
    >
      <AlternateCover releaseId={alt.release_id} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{alt.album}</div>
        {meta && (
          <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-white/40">
            {meta}
          </div>
        )}
      </div>
    </button>
  );
}

/**
 * "Different album?" picker shown in the AlternatesModal when audfprint
 * returned multiple candidate releases. Each row now includes a small
 * cover thumbnail from the local /art/<release_id> endpoint so the
 * user can distinguish pressings visually, not just by title/year.
 */
export function AlternatesList({ alternates, onSelect }: Props) {
  if (alternates.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-white/50">
        Different album?
      </div>
      <div className="flex flex-col gap-1.5">
        {alternates.map((alt) => (
          <AlternateRow key={alt.release_id} alt={alt} onSelect={onSelect} />
        ))}
      </div>
    </div>
  );
}
