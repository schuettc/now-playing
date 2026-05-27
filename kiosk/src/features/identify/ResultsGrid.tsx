import { AlbumCard } from './AlbumCard';
import type { GridCell, SearchRelease, SearchTrack } from './types';

interface Props {
  cells: GridCell[] | null;
  expandedReleaseId: number | null;
  highlightedTrackPosition: string | null;
  submittingTrackKey: string | null;
  onToggleExpanded: (releaseId: number) => void;
  onTrackPick: (rel: SearchRelease, t: SearchTrack) => void;
}

/**
 * Search-result grid: renders group headers + `AlbumCard`s in the
 * order produced by `useGridCells`. Returns `null` when the search is
 * empty (no input yet); renders a "No matches" placeholder when the
 * search returned but matched nothing.
 */
export function ResultsGrid({
  cells,
  expandedReleaseId,
  highlightedTrackPosition,
  submittingTrackKey,
  onToggleExpanded,
  onTrackPick,
}: Props) {
  if (cells === null) return null;
  if (cells.length === 0) {
    return (
      <div className="rounded-[12px] border border-dashed border-[#1f1f25] p-7 text-center text-sm text-[#8a8a95]">
        No matches.
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
      {cells.map((cell, idx) => {
        if (cell.kind === 'header') {
          return (
            <div
              key={`hdr-${cell.label}-${idx}`}
              className="col-span-full mt-2 flex items-baseline justify-between border-b border-[#1f1f25] pb-2"
            >
              <div className="text-[15px] font-semibold text-[#e9e9ee]">
                {cell.label}
              </div>
              <div className="rounded-full bg-[#6e8aff]/[0.18] px-2.5 py-0.5 text-xs font-semibold tabular-nums text-[#6e8aff]">
                {cell.count}
              </div>
            </div>
          );
        }
        const expanded = expandedReleaseId === cell.rel.release_id;
        return (
          <AlbumCard
            key={cell.rel.release_id}
            rel={cell.rel}
            expanded={expanded}
            highlightedTrackPosition={
              expanded ? highlightedTrackPosition : null
            }
            submittingTrackKey={submittingTrackKey}
            onToggleExpanded={() => onToggleExpanded(cell.rel.release_id)}
            onTrackPick={onTrackPick}
          />
        );
      })}
    </div>
  );
}
