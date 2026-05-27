import { useStore } from '@/store/useStore';
import { TrackRow } from '@/components/TrackRow';
import { sliceQueueWindow } from '@/lib/queue';

/**
 * Streaming "Queue" panel — recent plays (dimmed) + current
 * highlighted + upcoming tracks. The "QUEUE" eyebrow header has been
 * removed so the first row's top edge anchors on the shoulder line,
 * aligning with the title's top edge in the left column. AirPlay
 * sources don't populate `queue` (their queue lives on the sender
 * device), so this component renders nothing there.
 */
export function QueuePanel() {
  const queue = useStore((s) => s.payload?.queue);
  const currentIndex = useStore((s) => s.payload?.queue_position ?? -1);
  const rows = sliceQueueWindow(queue ?? [], currentIndex);

  if (rows.length === 0) return null;

  return (
    <div className="flex w-full flex-col gap-1 text-left">
      {rows.map((row) => (
        <TrackRow
          key={row.key}
          layoutId="queue-current-highlight"
          position={row.position}
          title={row.title}
          isCurrent={row.isCurrent}
          isDimmed={row.isDimmed}
        />
      ))}
    </div>
  );
}
