import type { QueueItem } from '@/types';

const RECENT_COUNT = 3;
const UPCOMING_COUNT = 6;

export interface QueueRow {
  key: string;
  position: string;
  title: string;
  isCurrent?: boolean;
  isDimmed?: boolean;
}

function toRow(
  item: QueueItem,
  absoluteIndex: number,
  flags: { isCurrent?: boolean; isDimmed?: boolean } = {},
): QueueRow {
  return {
    key: `q_${absoluteIndex}`,
    position: (absoluteIndex + 1).toString(),
    title: item.title ?? 'Unknown',
    ...flags,
  };
}

/**
 * Build the recent (dimmed) + current + upcoming window around
 * `currentIndex`. When `currentIndex` is negative the player position
 * is unknown — show the head of the queue as plain upcoming rows so the
 * panel doesn't look broken.
 */
export function sliceQueueWindow(
  items: QueueItem[],
  currentIndex: number,
): QueueRow[] {
  if (items.length === 0) return [];
  if (currentIndex < 0) {
    return items
      .slice(0, UPCOMING_COUNT)
      .map((item, i) => toRow(item, i));
  }
  const recentStart = Math.max(0, currentIndex - RECENT_COUNT);
  const recent = items
    .slice(recentStart, currentIndex)
    .map((item, i) => toRow(item, recentStart + i, { isDimmed: true }));
  const currentItem = items[currentIndex];
  const current = currentItem
    ? [toRow(currentItem, currentIndex, { isCurrent: true })]
    : [];
  const upcomingStart = currentIndex + 1;
  const upcoming = items
    .slice(upcomingStart, upcomingStart + UPCOMING_COUNT)
    .map((item, i) => toRow(item, upcomingStart + i));
  return [...recent, ...current, ...upcoming];
}
