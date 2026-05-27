/**
 * Bottom-left WebSocket connection indicator. Tiny on purpose — it's
 * an at-a-glance health dot, not a primary UI affordance. Includes its
 * own absolute positioning so callers don't have to wrap it.
 */
export function ConnectionDot({ connected }: { connected: boolean }) {
  return (
    <div className="absolute bottom-4 left-4 z-20">
      <div
        className={`h-1.5 w-1.5 rounded-full ${
          connected ? 'bg-emerald-400/60' : 'bg-white/10'
        }`}
        title={connected ? 'connected' : 'offline'}
      />
    </div>
  );
}
