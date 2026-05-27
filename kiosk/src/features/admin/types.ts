/**
 * One candidate image for the album-art picker. Streamed in from
 * `/api/art-candidates` via Server-Sent Events.
 */
export interface Candidate {
  url: string;
  source: 'current' | 'discogs-master' | 'discogs-release' | 'caa';
  label: string;
  width?: number;
  height?: number;
  release_id?: number;
}
