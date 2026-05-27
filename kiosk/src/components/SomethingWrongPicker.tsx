import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useLocation } from 'wouter';
import type { IdentifyState, NowPlaying } from '@/types';
import { useStore } from '@/store/useStore';
import {
  buildSomethingWrongRows,
  type SomethingWrongRow,
} from './somethingWrongPickerRows';
import { buildMatchInfo } from './somethingWrongMatchInfo';
import { ClearFingerprintsConfirm } from './ClearFingerprintsConfirm';

interface Props {
  data: NowPlaying;
  identifyState: IdentifyState;
  onClose: () => void;
  onChangeArt?: () => void;
  onOpenAlternates?: () => void;
}

function RowButton({
  row,
  onClick,
}: {
  row: SomethingWrongRow;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      data-testid={`something-wrong-row-${row.kind}`}
      className="flex min-h-[64px] flex-col items-start gap-1 rounded-xl bg-white/5 px-6 py-4 text-left text-white ring-1 ring-white/10 transition hover:bg-white/10"
    >
      <div className="text-sm font-semibold">{row.label}</div>
      <div className="text-xs text-white/60">{row.hint}</div>
    </button>
  );
}

function pickerSubline(data: NowPlaying): string | null {
  if (!data.album) return null;
  if (data.track_position) return `${data.album} · ${data.track_position}`;
  return data.album;
}

function MatchDetails({
  method,
  agoLabel,
}: {
  method: string;
  agoLabel: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/40">
        Match
      </div>
      <div className="font-mono text-[12px] tracking-[0.1em] text-white/70">
        {method}
        {agoLabel && (
          <span className="text-white/40"> · {agoLabel}</span>
        )}
      </div>
    </div>
  );
}

function PickerHeader({ data }: { data: NowPlaying }) {
  const subline = pickerSubline(data);
  const title = data.title ?? '—';
  const artist = data.artist ?? '—';
  return (
    <div className="flex flex-col gap-1">
      <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-white/40">
        Something wrong?
      </div>
      <div className="text-2xl font-semibold text-white">{title}</div>
      <div className="text-base text-white/70">{artist}</div>
      {subline && <div className="text-sm text-white/40">{subline}</div>}
    </div>
  );
}

/** Re-renders the ago label every 10s so "12s ago" → "22s ago" etc. */
function useNowTick(intervalMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

interface RowHandlers {
  onClose: () => void;
  navigate: (to: string) => void;
  onChangeArt?: () => void;
  onOpenAlternates?: () => void;
  onRequestClearFingerprints: () => void;
}

const ROW_ACTIONS: Record<
  SomethingWrongRow['kind'],
  (h: RowHandlers) => void
> = {
  // "Wrong track" opens the per-track identify view scoped to this
  // release — the user sees the full album tracklist and taps the
  // correct row. The right-column tracklist may only show one side at
  // a time, so this is the only flow that surfaces every side.
  'wrong-track': (h) => {
    h.onClose();
    h.navigate('/identify?from=admin&scope=track');
  },
  // "Wrong album" opens the AlternatesModal — a dedicated screen, not
  // an inline list. Picker closes; modal owns the user's attention
  // until they pick or cancel.
  'wrong-album': (h) => {
    h.onOpenAlternates?.();
  },
  // "Wrong song entirely" drops the album lock and opens the broad
  // identify view — no `scope=track` since the user is telling us
  // the current release context is meaningless.
  'wrong-song': (h) => {
    h.onClose();
    h.navigate('/identify?from=admin');
  },
  'change-art': (h) => h.onChangeArt?.(),
  // Stays inside the picker — opens the destructive-confirm sub-modal
  // on top so the user can cancel without losing the picker context.
  'clear-fingerprints': (h) => h.onRequestClearFingerprints(),
};

function handleRow(row: SomethingWrongRow, h: RowHandlers): void {
  ROW_ACTIONS[row.kind](h);
}

/**
 * Bottom-sheet picker that surfaces 1–4 "this is wrong" actions in
 * order of likelihood, plus a match-details block so the user can
 * see how + when the current track was identified before deciding
 * which row to tap.
 *
 *   1. Wrong track — open the album's tracklist scope (every side).
 *   2. Wrong album — open the AlternatesModal (only when payload
 *      includes alternate_releases).
 *   3. Wrong song entirely — open `/identify` (no scope, recent
 *      plays + search).
 *   4. Change album art — hand off to ArtPicker.
 *
 * Every row navigates to a dedicated screen — no inline lists in
 * this sheet. The picker is a router, not a workspace.
 */
type ClearResult = { ok: true } | { ok: false; reason: string };

async function postClearFingerprints(
  release_id: number,
  track_position: string,
): Promise<ClearResult> {
  try {
    const res = await fetch('/control/clear-fingerprints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id, track_position }),
    });
    if (res.ok) return { ok: true };
    return { ok: false, reason: 'clear-fingerprints-failed' };
  } catch {
    return { ok: false, reason: 'network' };
  }
}

export function SomethingWrongPicker({
  data,
  identifyState,
  onClose,
  onChangeArt,
  onOpenAlternates,
}: Props) {
  const [, navigate] = useLocation();
  const lastRecognizedAt = useStore((s) => s.lastRecognizedAt);
  const setPinErrorReason = useStore((s) => s.setPinErrorReason);
  const now = useNowTick();
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [clearing, setClearing] = useState(false);

  const match = buildMatchInfo(data.source, identifyState, lastRecognizedAt, now);
  const rows = buildSomethingWrongRows(data, onChangeArt !== undefined);
  const handlers: RowHandlers = {
    onClose,
    navigate,
    onChangeArt,
    onOpenAlternates,
    onRequestClearFingerprints: () => setConfirmingClear(true),
  };

  const handleClearConfirm = async () => {
    if (data.release_id === undefined || !data.track_position) {
      setConfirmingClear(false);
      return;
    }
    setClearing(true);
    const result = await postClearFingerprints(data.release_id, data.track_position);
    setClearing(false);
    setConfirmingClear(false);
    if (result.ok) {
      onClose();
    } else {
      setPinErrorReason(result.reason);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
      data-testid="something-wrong-picker"
    >
      <motion.div
        initial={{ y: 80 }}
        animate={{ y: 0 }}
        exit={{ y: 80 }}
        transition={{ duration: 0.3, ease: 'easeOut' }}
        onClick={(e) => e.stopPropagation()}
        className="m-12 flex max-h-[80vh] w-full max-w-2xl flex-col gap-6 overflow-y-auto rounded-2xl bg-zinc-900/90 p-10 ring-1 ring-white/10"
      >
        <PickerHeader data={data} />
        <MatchDetails method={match.method} agoLabel={match.agoLabel} />

        <div className="flex flex-col gap-3">
          {rows.map((row) => (
            <RowButton
              key={row.kind}
              row={row}
              onClick={() => handleRow(row, handlers)}
            />
          ))}
        </div>

        <button
          onClick={onClose}
          className="self-end font-mono text-[11px] uppercase tracking-[0.3em] text-white/40 transition hover:text-white/70"
        >
          Close
        </button>
      </motion.div>

      <AnimatePresence>
        {confirmingClear && (
          <ClearFingerprintsConfirm
            data={data}
            count={data.learned_fingerprint_count ?? 0}
            busy={clearing}
            onConfirm={handleClearConfirm}
            onCancel={() => setConfirmingClear(false)}
          />
        )}
      </AnimatePresence>
    </motion.div>
  );
}
