interface Props {
  album: string | null | undefined;
  artist: string | null | undefined;
  onClose: () => void;
}

export function ArtPickerHeader({ album, artist, onClose }: Props) {
  return (
    <header className="flex items-baseline justify-between gap-4">
      <div className="flex flex-col gap-0.5">
        <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-white/40">
          Change album art
        </div>
        <div className="text-xl font-semibold text-white">{album ?? '—'}</div>
        <div className="text-sm text-white/60">{artist ?? '—'}</div>
      </div>
      <button
        onClick={onClose}
        className="rounded-md px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.3em] text-white/50 transition hover:bg-white/5 hover:text-white"
      >
        Close
      </button>
    </header>
  );
}
