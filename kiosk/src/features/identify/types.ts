import type {
  SearchRelease,
  SearchResponse,
  SearchTrack,
} from '@/store/useStore';

export type { SearchRelease, SearchResponse, SearchTrack };

/**
 * One cell in the results grid. Headers introduce a group of releases
 * by an artist; release cells render an `AlbumCard`. The cell list is
 * built by `useGridCells` from the raw `SearchResponse`.
 */
export type GridCell =
  | { kind: 'header'; label: string; count: number }
  | { kind: 'release'; rel: SearchRelease };
