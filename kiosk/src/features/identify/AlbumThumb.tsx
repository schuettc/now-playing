import { useEffect, useState } from 'react';
import type { SearchRelease } from './types';

interface Props {
  rel: SearchRelease;
  size: 'square' | 'large';
}

/** Tailwind class string for the thumbnail based on display size. */
function buildThumbClass(size: 'square' | 'large'): string {
  return size === 'large'
    ? 'h-[180px] w-[180px] shrink-0 rounded-[10px] object-cover ring-1 ring-white/5'
    : 'aspect-square w-full rounded-t-[14px] object-cover';
}

/** Pixel dimensions for the img element, or undefined for square size. */
function buildThumbDimensions(size: 'square' | 'large'): number | undefined {
  return size === 'large' ? 180 : undefined;
}

/** Preferred art URL: explicit override first, then proxy path. */
function resolveThumbSrc(rel: SearchRelease): string {
  return rel.art_url ?? `/art/${rel.release_id}`;
}

function NoArtPlaceholder({ cls }: { cls: string }) {
  return (
    <div
      className={`${cls} flex items-center justify-center bg-zinc-900 text-[11px] uppercase tracking-wider text-zinc-500`}
    >
      no art
    </div>
  );
}

/**
 * Thumbnail used inside `AlbumCard` for the /identify results grid.
 * Prefer the explicit `art_url`; fall back to the `/art/<release_id>`
 * proxy. Renders a "no art" placeholder on load failure.
 *
 * Renamed from the previous in-route `AlbumArt` to end the name
 * collision with `components/AlbumArt` (the kiosk-wide album art
 * component used by the NowPlaying route).
 */
export function AlbumThumb({ rel, size }: Props) {
  const src = resolveThumbSrc(rel);
  const [failed, setFailed] = useState(false);
  const cls = buildThumbClass(size);
  const dim = buildThumbDimensions(size);

  useEffect(() => {
    setFailed(false);
  }, [rel.release_id]);

  if (failed) return <NoArtPlaceholder cls={cls} />;

  return (
    <img
      src={src}
      alt=""
      width={dim}
      height={dim}
      loading="lazy"
      onError={() => setFailed(true)}
      className={cls}
    />
  );
}
