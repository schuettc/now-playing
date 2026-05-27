import { useEffect, useRef, useState } from 'react';
import type { TracklistItem } from '@/types';
import { useIdentifyContext } from '@/hooks/identifyContext';
import { TappableTrackRow } from '@/components/tracklist/TappableTrackRow';
import { InlineError } from '@/components/feedback/InlineError';
import { track as telemetryTrack } from '@/lib/telemetry';
import { Centered, PageHeader } from './shared';
import { usePickedRef } from './pickedContext';

interface ScopedProps {
  releaseId: number;
  /**
   * The release_id from the current WebSocket payload. May be `undefined`
   * on initial render before the WS connection settles.
   */
  payloadReleaseId: number | undefined;
  /**
   * The tracklist to display. `null` = still loading (show spinner).
   * `[]` = loaded but no tracks in catalog.
   */
  tracks: TracklistItem[] | null;
  currentPosition: string | null;
  guessPosition: string | null;
  onDone: () => void;
}

function PendingAlbumCheck({ onDone }: { onDone: () => void }) {
  const [showTerminal, setShowTerminal] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setShowTerminal(true), 1000);
    return () => clearTimeout(t);
  }, []);
  if (!showTerminal) return <Centered>Loading…</Centered>;
  return <AlbumChanged onDone={onDone} />;
}

function AlbumChanged({ onDone }: { onDone: () => void }) {
  return (
    <Centered>
      <div className="flex flex-col items-center gap-4">
        <span
          className="font-mono text-[11px] uppercase tracking-[0.3em]"
          style={{ color: 'var(--text-tertiary)' }}
        >
          Album changed
        </span>
        <button
          type="button"
          onClick={onDone}
          className="rounded-[10px] px-6 py-3 text-[16px]"
          style={{
            background: 'var(--text-hairline)',
            color: 'var(--text-primary)',
          }}
        >
          Back to now playing
        </button>
      </div>
      <InlineError />
    </Centered>
  );
}

interface ScopedTracklistProps {
  releaseId: number;
  /** Non-null: caller asserts tracks are loaded before rendering this. */
  tracks: TracklistItem[];
  currentPosition: string | null;
  guessPosition: string | null;
  onDone: () => void;
  /** True when the tapped release is the currently playing album. Controls
   *  whether TappableTrackRow calls /api/pin-track or /api/identify. */
  isCurrentAlbum: boolean;
}

/**
 * Watches for a new pin landing and fires navigation + telemetry.
 * Mount-time gate: only reacts when `pinPosition` changes from the
 * value seen at mount so a stale 50s pin overlay doesn't bounce the
 * user out immediately on arrival.
 */
function useScopedPinNavigation(onDone: () => void) {
  const { pinPosition } = useIdentifyContext();
  const pickedRef = usePickedRef();
  const initialPinPositionRef = useRef<string | null>(pinPosition);
  const mountedAtRef = useRef(Date.now());
  useEffect(() => {
    if (pinPosition === null || pinPosition === initialPinPositionRef.current) return;
    pickedRef.current = true;
    telemetryTrack('identify_lookup_pick', {
      variant: 'scoped',
      picked_album: false,
      picked_track: true,
      ms_to_pick: Date.now() - mountedAtRef.current,
    });
    const t = setTimeout(onDone, 400);
    return () => clearTimeout(t);
  }, [pinPosition, onDone, pickedRef]);
}

function ScopedTracklist({
  releaseId, tracks, currentPosition, guessPosition, onDone, isCurrentAlbum,
}: ScopedTracklistProps) {
  useScopedPinNavigation(onDone);

  return (
    <div
      className="h-screen w-screen overflow-y-auto px-12 py-10"
      style={{ background: 'var(--bg-base)' }}
    >
      <div className="mx-auto flex max-w-[800px] flex-col gap-4">
        <PageHeader eyebrow="Pick a track" onBack={onDone} />
        <div className="flex flex-col gap-1">
          {tracks.map((t) => (
            <TappableTrackRow
              key={t.position}
              releaseId={releaseId}
              position={t.position}
              title={t.title}
              durationSeconds={t.duration_seconds}
              currentPosition={currentPosition}
              guessPosition={guessPosition}
              isCurrentAlbum={isCurrentAlbum}
            />
          ))}
        </div>
        {tracks.length === 0 && (
          <div
            className="py-8 text-center"
            style={{ color: 'var(--text-secondary)' }}
          >
            No tracklist available for this album.
          </div>
        )}
      </div>
      <InlineError />
    </div>
  );
}

/**
 * Scoped fast path: `/lookup?release=<rid>`. Renders the album's
 * tracklist as `TappableTrackRow`s. Tap → identify/pin → bounce
 * back to `/`. Shipped in D-4; extended in D-5 to support past-album
 * recents taps where `payloadReleaseId` may differ at mount.
 *
 * Album-changed guard logic:
 * - If `payloadReleaseId` matches `releaseId` at mount (current album)
 *   and then diverges later (album changed), show PendingAlbumCheck.
 * - If `payloadReleaseId` already differed at mount (past-album recents
 *   tap), show the tracklist directly — the user explicitly navigated here.
 * - `payloadReleaseId` may be `undefined` for several frames before the
 *   WS payload settles. We track the *first non-undefined* value seen;
 *   until then we render the tracklist so the user doesn't see a flash.
 *
 * Loading guard:
 * - `tracks === null` means the API fetch is still in-flight. Show spinner.
 */
export function LookupViewScoped({
  releaseId, payloadReleaseId, tracks, currentPosition, guessPosition, onDone,
}: ScopedProps) {
  // Track the first non-undefined payloadReleaseId seen after mount.
  // We use this to distinguish "WS not settled yet" (undefined) from
  // "truly different album at mount" (truthy but !== releaseId).
  const firstKnownPayloadIdRef = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (firstKnownPayloadIdRef.current === undefined && payloadReleaseId !== undefined) {
      firstKnownPayloadIdRef.current = payloadReleaseId;
    }
  }, [payloadReleaseId]);

  // Was the current album matching this release when the payload first settled?
  // Only activate the "album changed" guard for sessions that started as current.
  const wasMatchingAtMount = firstKnownPayloadIdRef.current === releaseId;
  const albumChangedAfterMount = wasMatchingAtMount && payloadReleaseId !== releaseId;

  if (albumChangedAfterMount) {
    // Grace period: don't show 'Album changed' instantly. Fast WS
    // payloads can race with the URL navigation.
    return <PendingAlbumCheck onDone={onDone} />;
  }

  // `tracks === null` = API fetch still in-flight (past album path).
  if (tracks === null) return <Centered>Loading…</Centered>;

  // `isCurrentAlbum` tells TappableTrackRow whether to use /api/pin-track
  // (fast path for the locked album) or /api/identify (for past albums).
  const isCurrentAlbum = payloadReleaseId === releaseId;

  return (
    <ScopedTracklist
      releaseId={releaseId}
      tracks={tracks}
      currentPosition={currentPosition}
      guessPosition={guessPosition}
      onDone={onDone}
      isCurrentAlbum={isCurrentAlbum}
    />
  );
}
