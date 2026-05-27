import { useCallback, useState } from 'react';
import type { Candidate } from './types';
import {
  buildPickBody,
  buildResetQuery,
  deleteArtOverride,
  postArtOverride,
} from './artOverrideApi';

interface Args {
  releaseId: number | undefined;
  artist: string | undefined;
  album: string | undefined;
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
  onClose: () => void;
}

interface RunCtx {
  ids: { releaseId: number | undefined; artist: string | undefined; album: string | undefined };
  setError: (msg: string) => void;
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
  onClose: () => void;
}

async function runPick(c: Candidate, ctx: RunCtx): Promise<void> {
  if (c.source === 'current') {
    ctx.onClose();
    return;
  }
  const result = await postArtOverride(buildPickBody(c, ctx.ids));
  if (!result.ok) {
    ctx.setError(result.error ?? 'Could not save your choice.');
    return;
  }
  ctx.onSaved(Date.now(), result.overrideUrl);
  ctx.onClose();
}

async function runReset(ctx: RunCtx): Promise<void> {
  const result = await deleteArtOverride(buildResetQuery(ctx.ids));
  if (!result.ok) {
    ctx.setError(result.error ?? 'Could not reset to default.');
    return;
  }
  ctx.onSaved(Date.now());
  ctx.onClose();
}

/**
 * Owns the POST `/api/art-override` (pick) and DELETE
 * `/api/art-override` (reset) round-trips for the album-art picker.
 * `busy` blocks both buttons during in-flight requests; `error` holds
 * a user-readable message after a failed save (cleared on the next
 * attempt). `c.source === 'current'` short-circuits pick to a no-op
 * close.
 */
export function useArtOverride({ releaseId, artist, album, onSaved, onClose }: Args) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canFetch = Boolean(releaseId) || Boolean(artist && album);

  const pick = useCallback(
    async (c: Candidate) => {
      if (!canFetch || busy) return;
      setBusy(true);
      setError(null);
      try {
        await runPick(c, {
          ids: { releaseId, artist, album },
          setError,
          onSaved,
          onClose,
        });
      } finally {
        setBusy(false);
      }
    },
    [busy, canFetch, releaseId, artist, album, onSaved, onClose],
  );

  const reset = useCallback(async () => {
    if (!canFetch || busy) return;
    setBusy(true);
    setError(null);
    try {
      await runReset({
        ids: { releaseId, artist, album },
        setError,
        onSaved,
        onClose,
      });
    } finally {
      setBusy(false);
    }
  }, [busy, canFetch, releaseId, artist, album, onSaved, onClose]);

  return { pick, reset, busy, error };
}
