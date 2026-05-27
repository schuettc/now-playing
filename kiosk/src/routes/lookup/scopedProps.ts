import type { NowPlaying, TracklistItem } from '@/types';

interface ScopedPositionFields {
  payloadReleaseId: number | undefined;
  currentPosition: string | null;
  guessPosition: string | null;
}

const NULL_POSITION_FIELDS: ScopedPositionFields = {
  payloadReleaseId: undefined,
  currentPosition: null,
  guessPosition: null,
};

/** Returns `pos` when this payload's release matches `releaseId`, else null.
 *  Prevents track highlights from a different currently-playing album
 *  bleeding into a past-album tracklist view. */
function positionIfMatch(
  pos: string | null | undefined,
  releaseMatches: boolean,
): string | null {
  return releaseMatches ? (pos ?? null) : null;
}

function positionFieldsFromPayload(
  p: NowPlaying,
  releaseId: number,
): ScopedPositionFields {
  const releaseMatches = p.release_id === releaseId;
  return {
    payloadReleaseId: p.release_id,
    currentPosition: positionIfMatch(p.track_position, releaseMatches),
    guessPosition: positionIfMatch(p.guess?.position, releaseMatches),
  };
}

/**
 * Choose the tracklist source for the scoped view.
 * When `payload.release_id === releaseId` the WS payload already has
 * the correct tracks (no extra round-trip). Otherwise use `apiTracks`
 * fetched from `/api/release/<id>/tracklist`.
 */
function tracksForScoped(
  releaseId: number,
  payload: NowPlaying | null | undefined,
  apiTracks: TracklistItem[] | null,
): TracklistItem[] | null {
  if (payload?.release_id === releaseId) return payload.tracklist ?? [];
  return apiTracks;
}

export interface ScopedPropsResult {
  releaseId: number;
  tracks: TracklistItem[] | null;
  payloadReleaseId: number | undefined;
  currentPosition: string | null;
  guessPosition: string | null;
}

/**
 * Pure helper that derives the props passed to `LookupViewScoped`.
 *
 * The `fromNeedsId` flag is the key decision. When set, the user
 * arrived here from "Help identify this song" on the NEEDS_ID screen,
 * which means the cascade's current/guess positions are by definition
 * the stale guess that prompted the bailout. Highlighting them on the
 * tracklist would prejudice a tap toward exactly the wrong track
 * (the silent-pin bug from docs/features/recents-one-tap-silent-pin/).
 * So we strip those highlights and force a deliberate, unguided pick.
 *
 * When `fromNeedsId` is false the original behaviour stands — the
 * highlights help users confirm the currently-playing track on a
 * release they explicitly chose.
 */
export function pickScopedProps(
  releaseId: number,
  payload: NowPlaying | null | undefined,
  apiTracks: TracklistItem[] | null,
  fromNeedsId: boolean,
): ScopedPropsResult {
  const tracks = tracksForScoped(releaseId, payload, apiTracks);
  const positionFields = payload
    ? positionFieldsFromPayload(payload, releaseId)
    : NULL_POSITION_FIELDS;
  if (fromNeedsId) {
    return {
      releaseId,
      tracks,
      payloadReleaseId: positionFields.payloadReleaseId,
      currentPosition: null,
      guessPosition: null,
    };
  }
  return { releaseId, tracks, ...positionFields };
}
