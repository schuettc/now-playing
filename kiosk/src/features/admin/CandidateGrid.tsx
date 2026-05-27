import type { Candidate } from './types';

const SOURCE_CHIP_CLASS: Record<Candidate['source'], string> = {
  current: 'bg-white/10 text-white/70 ring-white/20',
  'discogs-master': 'bg-amber-500/20 text-amber-100 ring-amber-500/30',
  'discogs-release': 'bg-sky-500/20 text-sky-100 ring-sky-500/30',
  caa: 'bg-emerald-500/20 text-emerald-100 ring-emerald-500/30',
};

interface Props {
  candidates: Candidate[];
  streamDone: boolean;
  busy: boolean;
  onPick: (c: Candidate) => void;
}

/**
 * Scrollable grid of candidate album-art images. Shows skeleton
 * placeholders while the SSE stream is still emitting, and an empty
 * state once the stream completes with no results.
 */
export function CandidateGrid({ candidates, streamDone, busy, onPick }: Props) {
  return (
    <div className="grid flex-1 auto-rows-min grid-cols-2 gap-4 overflow-y-auto md:grid-cols-3">
      {candidates.map((c) => (
        <button
          key={c.url}
          onClick={() => onPick(c)}
          disabled={busy}
          className="group flex flex-col gap-2 rounded-xl bg-white/5 p-2 text-left ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-50"
        >
          <div className="aspect-square w-full overflow-hidden rounded-lg bg-black/40">
            <img
              src={c.url}
              alt={c.label}
              loading="lazy"
              className="h-full w-full object-cover"
              onError={(e) => {
                (e.currentTarget.parentElement as HTMLElement).style.opacity =
                  '0.35';
              }}
            />
          </div>
          <div className="flex items-center justify-between gap-2 px-1">
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.2em] ring-1 ${SOURCE_CHIP_CLASS[c.source]}`}
            >
              {c.label}
            </span>
            {c.width && c.height && (
              <span className="font-mono text-[10px] text-white/40">
                {c.width}×{c.height}
              </span>
            )}
          </div>
        </button>
      ))}
      {!streamDone &&
        Array.from({ length: Math.max(0, 6 - candidates.length) }).map(
          (_, i) => (
            <div
              key={`skel-${i}`}
              className="aspect-square animate-pulse rounded-xl bg-white/5"
            />
          ),
        )}
      {streamDone && candidates.length === 0 && (
        <div className="col-span-full py-12 text-center text-sm text-white/40">
          No alternative art found for this album.
        </div>
      )}
    </div>
  );
}
