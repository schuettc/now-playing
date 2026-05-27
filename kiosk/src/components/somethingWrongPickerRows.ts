import type { NowPlaying } from '@/types';

export type SomethingWrongRowKind =
  | 'wrong-track'
  | 'wrong-album'
  | 'wrong-song'
  | 'change-art'
  | 'clear-fingerprints';

export interface SomethingWrongRow {
  kind: SomethingWrongRowKind;
  label: string;
  hint: string;
}

const TEMPLATES: Record<SomethingWrongRowKind, Omit<SomethingWrongRow, 'kind'>> = {
  'wrong-track': {
    label: 'Wrong track',
    hint: 'See the full tracklist for this album and pick the right one.',
  },
  'wrong-album': {
    label: 'Wrong album',
    hint: 'Same track on a different pressing.',
  },
  'wrong-song': {
    label: 'Wrong song entirely',
    hint: 'Browse recent plays or search the catalog from scratch.',
  },
  'change-art': {
    label: 'Change album art',
    hint: 'Pick from Discogs master, alternate pressings, or Cover Art Archive.',
  },
  'clear-fingerprints': {
    label: 'Forget what I taught the system here',
    hint: 'Delete the fingerprints learned for this track. It will re-learn next time you play it.',
  },
};

/**
 * Derives the picker rows for the current payload. Rows hide when not
 * applicable so the sheet never shows an empty/non-actionable option.
 *
 * Rules (in order of likelihood):
 *   - 'wrong-track' when there's a tracklist with > 1 entry on a
 *     vinyl source with a known release_id (the row navigates to
 *     `/identify?from=admin&scope=track`, which needs the release).
 *   - 'wrong-album' when `alternate_releases` is non-empty (vinyl
 *     only — non-vinyl sources don't carry alternates).
 *   - 'wrong-song' always shown when the picker is open (re-identify
 *     is the universal escape hatch).
 *   - 'change-art' when the parent passed an `onChangeArt` handler
 *     AND the payload has a `release_id` or `album` to pin art against.
 */
export function buildSomethingWrongRows(
  data: NowPlaying,
  canChangeArt: boolean,
): SomethingWrongRow[] {
  const out: SomethingWrongRow[] = [];
  const isVinyl = data.source === 'vinyl';
  const hasTracklist =
    isVinyl &&
    (data.tracklist?.length ?? 0) > 1 &&
    data.release_id !== undefined;
  const hasAlternates =
    isVinyl && (data.alternate_releases?.length ?? 0) > 0;
  const hasArtTarget =
    canChangeArt && (data.release_id !== undefined || Boolean(data.album));

  if (hasTracklist) {
    out.push({ kind: 'wrong-track', ...TEMPLATES['wrong-track'] });
  }
  if (hasAlternates) {
    out.push({ kind: 'wrong-album', ...TEMPLATES['wrong-album'] });
  }
  // Wrong-song-entirely only makes sense when recognition can be
  // wrong — i.e. vinyl. AirPlay / streaming / TV get metadata
  // straight from the source device, so the song name is
  // authoritative by definition. Browsing /identify wouldn't help
  // the user (they'd need to change what's playing on their phone /
  // streaming app, not the kiosk).
  if (isVinyl) {
    out.push({ kind: 'wrong-song', ...TEMPLATES['wrong-song'] });
  }
  if (hasArtTarget) {
    out.push({ kind: 'change-art', ...TEMPLATES['change-art'] });
  }
  // Clear-fingerprints is destructive and recovery-shaped — surface
  // only when there's actually something to clear. The backend stamps
  // learned_fingerprint_count on every payload with a known
  // (release_id, track_position); missing or zero means no refs and
  // we hide the row.
  if ((data.learned_fingerprint_count ?? 0) > 0) {
    out.push({ kind: 'clear-fingerprints', ...TEMPLATES['clear-fingerprints'] });
  }
  return out;
}
