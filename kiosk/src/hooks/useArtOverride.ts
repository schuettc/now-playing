import { useCallback, useEffect, useState } from 'react';

/**
 * Bridges the ~5s gap between an ArtPicker save and the next WS push
 * that confirms the new art_url. After a save:
 *  - `artCacheBust` rises so any cache-busted URL refetches.
 *  - `artUrlOverride` (when supplied) is the freshly-saved server URL;
 *    the kiosk crossfades to it immediately instead of continuing to
 *    show the Sonos-supplied URL for another five seconds.
 *
 * Both are cleared on the next `trackId` change so a stale override
 * from track A can't shadow track B's real art_url.
 */
export function useArtOverride(args: {
  trackId: string;
  artUrl: string | undefined;
}): {
  effectiveArtUrl: string | undefined;
  artCacheBust: number;
  onSaved: (cacheBust: number, overrideUrl?: string) => void;
} {
  const { trackId, artUrl } = args;
  const [artCacheBust, setArtCacheBust] = useState(0);
  const [artUrlOverride, setArtUrlOverride] = useState<string | undefined>(
    undefined,
  );

  useEffect(() => {
    setArtUrlOverride(undefined);
  }, [trackId]);

  const onSaved = useCallback(
    (cacheBust: number, overrideUrl?: string) => {
      setArtCacheBust(cacheBust);
      if (overrideUrl) setArtUrlOverride(overrideUrl);
    },
    [],
  );

  return {
    effectiveArtUrl: artUrlOverride ?? artUrl,
    artCacheBust,
    onSaved,
  };
}
