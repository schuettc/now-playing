import type { NowPlaying } from '@/types';

const FAILURE_ART = '/fixtures/fantastic-planet.svg';
const RADIOHEAD_ART = '/fixtures/in-rainbows.svg';
const AFGHAN_WHIGS_ART = '/fixtures/gentlemen.svg';

export const fixtures = {
  vinyl: {
    ts: '2026-05-09T22:14:33Z',
    state: 'PLAYING',
    source: 'vinyl',
    title: 'Heliotropic',
    artist: 'Failure',
    album: 'Fantastic Planet',
    year: 1996,
    art_url: FAILURE_ART,
    release_id: 1095684,
    label: 'Slash',
    catno: '828 715-2',
    track_position: 'C13',
    side: 'C',
    match_method: 'shazam',
    match_confidence: 142,
    tracklist: [
      { position: 'C11', side: 'C', title: 'Stuck on You', duration_seconds: 188 },
      { position: 'C12', side: 'C', title: 'Sergeant Politeness', duration_seconds: 200 },
      { position: 'C13', side: 'C', title: 'Heliotropic', duration_seconds: 247 },
      { position: 'C14', side: 'C', title: 'Pillowhead', duration_seconds: 271 },
    ],
  },
  streaming: {
    ts: '2026-05-09T22:14:33Z',
    state: 'PLAYING',
    source: 'streaming',
    title: 'Weird Fishes/Arpeggi',
    artist: 'Radiohead',
    album: 'In Rainbows',
    year: 2007,
    art_url: RADIOHEAD_ART,
    match_method: 'sonos-didl',
  },
  shazam: {
    ts: '2026-05-09T22:14:33Z',
    state: 'PLAYING',
    source: 'vinyl',
    title: 'Be Sweet',
    artist: 'The Afghan Whigs',
    album: 'Gentlemen',
    year: 1993,
    art_url: AFGHAN_WHIGS_ART,
    release_id: 372810,
    track_position: 'A1',
    side: 'A',
    match_method: 'shazam',
    match_confidence: 'hit',
  },
  airplay: {
    ts: '2026-05-09T22:14:33Z',
    state: 'PLAYING',
    source: 'airplay',
    device_name: "Court's iPhone",
    match_method: 'sonos-didl',
  },
  idle: {
    ts: '2026-05-09T22:14:33Z',
    state: 'STOPPED',
    source: 'unknown',
  },
  'needs-id': {
    ts: '2026-05-11T16:09:50Z',
    state: 'NEEDS_ID',
    source: 'vinyl',
    match_method: 'unmatched',
    previous: {
      release_id: 7405236,
      track_position: 'C1',
      title: '$300',
      artist: 'Soul Coughing',
    },
  },
} satisfies Record<string, NowPlaying>;

export type FixtureKey = keyof typeof fixtures;
