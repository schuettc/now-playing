import { useEffect } from 'react';
import { useStore } from '@/store/useStore';
import { formatRelativeTime } from '@/utils/format';

// Any last-play within this window of "now" for the currently-playing
// album is treated as part of the current listening session, not history.
const SESSION_WINDOW_S = 30 * 60;

/** "Played N time(s)" label. */
function formatPlayCount(count: number): string {
  return `Played ${count} ${count === 1 ? 'time' : 'times'}`;
}

/**
 * "Last <relative>" label, or null when the last play is within the
 * current session window (same album, played < SESSION_WINDOW_S ago).
 */
function formatLastLabel(
  releaseId: number | undefined,
  lastPlayedAt: number | null,
  statsReleaseId: number | undefined,
  nowS: number,
): string | null {
  if (lastPlayedAt === null) return null;
  const inSession =
    statsReleaseId === releaseId && nowS - lastPlayedAt < SESSION_WINDOW_S;
  if (inSession) return null;
  return `Last ${formatRelativeTime(lastPlayedAt)}`;
}

/**
 * Mono caption that lives directly under the album art. Replaces the
 * old StatsPanel card in the left column — pulling the listening
 * history out of the metadata column and pairing it with the object
 * it describes (the album art) means it doesn't compete with the
 * wiki blurb for attention.
 *
 * Format: "PLAYED N TIMES  ·  LAST 3 DAYS AGO"
 *
 * The whole caption is a link to the dashboard focused on this
 * release, matching the prior StatsPanel behavior.
 */
export function StatsCaption() {
  const rid = useStore((s) => s.payload?.release_id);
  const stats = useStore((s) =>
    rid !== undefined ? s.albumStats.get(rid) ?? null : null,
  );
  const fetchAlbumStats = useStore((s) => s.fetchAlbumStats);

  useEffect(() => {
    if (rid === undefined) return;
    fetchAlbumStats(rid);
  }, [rid, fetchAlbumStats]);

  if (!stats || !stats.play_count) return null;

  const playLabel = formatPlayCount(stats.play_count);
  const lastLabel = formatLastLabel(
    rid,
    stats.last_played_at,
    stats.release_id,
    Date.now() / 1000,
  );

  return (
    <a
      href={`/dashboard?focus=${rid}`}
      className="block whitespace-nowrap text-center font-mono text-[13px] uppercase tracking-[0.32em] text-white/40 transition-colors hover:text-white/70"
    >
      {playLabel}
      {lastLabel ? <>{'  ·  '}{lastLabel}</> : null}
    </a>
  );
}
