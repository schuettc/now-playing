import type { NowPlaying } from '@/types';
import { useArtCandidates } from '@/features/admin/useArtCandidates';
import { useArtOverride } from '@/features/admin/useArtOverride';
import { CandidateGrid } from '@/features/admin/CandidateGrid';
import { ArtPickerFooter } from '@/features/admin/ArtPickerFooter';
import { useEscapeKey } from '@/hooks/useEscapeKey';
import { ArtPickerShell } from './ArtPickerShell';
import { ArtPickerHeader } from './ArtPickerHeader';
import { ArtPickerError } from './ArtPickerError';

interface Props {
  data: NowPlaying;
  onClose: () => void;
  /**
   * Called after a successful save. `cacheBust` is a timestamp the
   * parent uses to force the visible art `<img>` to refetch.
   * `overrideUrl` is the new server URL (e.g. `/art/123?v=…` or
   * `/art-by-name?…&v=…`) — the parent should crossfade to this URL
   * until the next WS push confirms the same via `art_url`.
   */
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
}

/**
 * Full-screen modal for picking an alternate album-art image. Streams
 * candidates from `/api/art-candidates`, posts the user's pick to
 * `/api/art-override`, supports a "Reset to default" DELETE. All
 * stateful behavior lives in `features/admin/use{ArtCandidates,
 * ArtOverride}` — this component is presentation only.
 */
export function ArtPicker({ data, onClose, onSaved }: Props) {
  const releaseId = data.release_id;
  const byName = !releaseId && Boolean(data.artist && data.album);
  const canFetch = Boolean(releaseId) || byName;

  const { candidates, streamDone } = useArtCandidates({
    releaseId,
    artist: data.artist,
    album: data.album,
    currentArtUrl: data.art_url,
    enabled: canFetch,
  });
  const { pick, reset, busy, error } = useArtOverride({
    releaseId,
    artist: data.artist,
    album: data.album,
    onSaved,
    onClose,
  });

  useEscapeKey(onClose);

  return (
    <ArtPickerShell onClose={onClose}>
      <ArtPickerHeader
        album={data.album}
        artist={data.artist}
        onClose={onClose}
      />
      <ArtPickerError error={error} />
      <CandidateGrid
        candidates={candidates}
        streamDone={streamDone}
        busy={busy}
        onPick={pick}
      />
      <ArtPickerFooter
        candidates={candidates}
        streamDone={streamDone}
        busy={busy}
        onReset={reset}
      />
    </ArtPickerShell>
  );
}
