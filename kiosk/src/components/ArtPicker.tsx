import { useState } from 'react';
import type { NowPlaying } from '@/types';
import { useArtCandidates } from '@/features/admin/useArtCandidates';
import { useArtOverride } from '@/features/admin/useArtOverride';
import { artTargetOf } from '@/features/admin/artOverrideApi';
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
  // Captured when the picker opens: the candidates shown and the save both
  // belong to this album, whatever starts playing while the grid is up.
  const [target] = useState(() => artTargetOf(data));
  const { releaseId, artist, album } = target;
  const canFetch = Boolean(releaseId) || Boolean(artist && album);

  const { candidates, streamDone } = useArtCandidates({ ...target, enabled: canFetch });
  const { pick, reset, busy, error } = useArtOverride({
    releaseId,
    artist,
    album,
    onSaved,
    onClose,
  });

  useEscapeKey(onClose);

  return (
    <ArtPickerShell onClose={onClose}>
      <ArtPickerHeader
        album={album}
        artist={artist}
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
