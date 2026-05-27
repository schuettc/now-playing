import type { Candidate } from './types';

interface Props {
  candidates: Candidate[];
  streamDone: boolean;
  busy: boolean;
  onReset: () => void;
}

/**
 * Footer for the ArtPicker modal: a "Reset to default" button on the
 * left and a counter on the right that reads "N options" when the SSE
 * stream is done or "Loading more…" while it's still active.
 */
export function ArtPickerFooter({
  candidates,
  streamDone,
  busy,
  onReset,
}: Props) {
  return (
    <footer className="flex items-center justify-between gap-4 pt-2">
      <button
        onClick={onReset}
        disabled={busy}
        className="rounded-lg bg-white/5 px-4 py-2 text-sm text-white/70 ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-50"
      >
        Reset to default
      </button>
      <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/30">
        {streamDone
          ? `${candidates.length} option${candidates.length === 1 ? '' : 's'}`
          : 'Loading more…'}
      </div>
    </footer>
  );
}
