import type { ReactNode } from 'react';
import { AnimatePresence } from 'framer-motion';
import type { Guess, NowPlaying } from '@/types';
import { AlbumArt } from '@/components/AlbumArt';
import { TrackInfo } from '@/components/TrackInfo';
import { StatsCaption } from '@/components/StatsCaption';
import { TracklistPanel } from '@/components/TracklistPanel';
import { QueuePanel } from '@/components/QueuePanel';
import { PauseIndicator } from '@/components/PauseIndicator';
import { GuessConfirmCard } from '@/components/guess';
import { withCacheBust } from '@/lib/art';

interface Props {
  data: NowPlaying;
  artId: string;
  trackId: string;
  effectiveArtUrl: string | undefined;
  artCacheBust: number;
  isPaused: boolean;
  /** When set, the left column shows the guess card instead of TrackInfo. */
  guess?: Guess | null;
}

// Strategy D — Shoulder Line. The side columns occupy the vertical
// middle 60% of the album art's height (shoulder at 18% from the art's
// top, knee at 78%). The title row's top edge and the first track
// row's top edge both anchor on the shoulder line, so the eye picks
// up a clean horizontal "shoulder" across all three columns.
//
// Each side column is wrapped in an art-height container so the outer
// grid's `items-center` lines its frame up with the (also-centered)
// album art. Inside that wrapper, the actual content is
// absolute-positioned at the shoulder offset with a fixed knee-to-
// shoulder height, plus a bottom alpha mask so overflowing content
// (long wiki blurbs, White-Album-class tracklists) fades into the
// backdrop rather than hard-clipping.
const SHOULDER_TOP = '18%';
const SHOULDER_HEIGHT = '60%';

export function TrackLayout({
  data,
  artId,
  trackId,
  effectiveArtUrl,
  artCacheBust,
  isPaused,
  guess,
}: Props) {
  const bustedUrl = withCacheBust(effectiveArtUrl, artCacheBust);

  return (
    <div className="relative z-10 grid h-full w-full grid-cols-[400px_minmax(0,840px)_400px] items-center justify-center gap-14">
      {/* LEFT column: TrackInfo + optional guess card */}
      <ShoulderColumn>
        <TrackInfo key={`info-${trackId}`} data={data} identity={trackId} />
        <AnimatePresence>
          {guess && (
            <div className="mt-8 w-full">
              <GuessConfirmCard key={`guess-${guess.position}`} guess={guess} />
            </div>
          )}
        </AnimatePresence>
      </ShoulderColumn>

      {/* CENTER — album art + listening-history caption directly below */}
      <div className="flex flex-col items-center gap-3.5">
        <div className="relative">
          <AlbumArt
            src={bustedUrl}
            alt={data.album ?? data.title}
            identity={`${artId}#${artCacheBust}`}
            dim={isPaused}
          />
          <AnimatePresence>
            {isPaused && <PauseIndicator key="pause-overlay" />}
          </AnimatePresence>
        </div>
        <StatsCaption />
      </div>

      {/* RIGHT — queue or tracklist, shoulder-to-knee bounds, no
          eyebrow header so first row aligns with title top edge */}
      <ShoulderColumn>
        <RightColumn source={data.source} />
      </ShoulderColumn>
    </div>
  );
}

/**
 * Side-column shell. The outer frame is the full art height (matched
 * to the center column's 85vh) so the parent grid's items-center
 * lines its top + bottom edges up with the album art. The inner
 * content area is absolute-positioned at the shoulder/knee offsets
 * and has the overflow mask applied — content that exceeds the
 * shoulder-to-knee window fades into the backdrop at the bottom.
 *
 * Background-agnostic by design: the mask fades the content's
 * alpha rather than painting a color over it, so whatever tinted
 * backdrop is behind shows through naturally.
 */
function ShoulderColumn({ children }: { children: ReactNode }) {
  const mask =
    'linear-gradient(to bottom, black 0%, black calc(100% - 80px), transparent 100%)';
  return (
    <div className="relative h-[85vh] max-h-[840px] w-[400px]">
      <div
        className="absolute inset-x-0 flex flex-col items-start overflow-hidden text-left"
        style={{
          top: SHOULDER_TOP,
          height: SHOULDER_HEIGHT,
          maskImage: mask,
          WebkitMaskImage: mask,
        }}
      >
        {children}
      </div>
    </div>
  );
}

// Source determines the right-column content:
//   vinyl     → Discogs tracklist with current track highlighted
//   streaming → Up Next panel from the Sonos queue
//   airplay   → nothing (queue lives on the sender device)
// AirPlay still allocates the column track in the grid so the album
// art holds its horizontal center position.
function RightColumn({ source }: { source: NowPlaying['source'] }) {
  if (source === 'vinyl') return <TracklistPanel />;
  if (source === 'streaming') return <QueuePanel />;
  return null;
}
