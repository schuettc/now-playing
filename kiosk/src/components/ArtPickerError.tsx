export function ArtPickerError({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-200 ring-1 ring-rose-500/30">
      {error}
    </div>
  );
}
