/**
 * Shared layout primitives for the LookupView variants.
 *
 * `Centered` and `BackButton` are used by both the scoped and
 * unscoped paths; pulling them up avoids divergence as the
 * variants evolve.
 */
import { useLocation } from 'wouter';
import { InlineError } from '@/components/feedback/InlineError';
import { SearchSeedProvider } from './searchSeedContext';
import type { SearchHandle } from './SearchSection';

interface CenteredProps {
  children: React.ReactNode;
}

export function Centered({ children }: CenteredProps) {
  return (
    <div
      className="flex h-screen w-screen items-center justify-center"
      style={{ background: 'var(--bg-base)', color: 'var(--text-body)' }}
    >
      {children}
    </div>
  );
}

interface PageHeaderProps {
  eyebrow: string;
  onBack: () => void;
}

interface LookupShellProps {
  search: SearchHandle;
  /** Vertical gap between scaffold children. */
  gap?: 'md' | 'lg';
  children: React.ReactNode;
}

/**
 * Shared scaffold for all three unscoped LookupView variants:
 * SearchSeedProvider wrap, full-screen background, max-width
 * column, PageHeader, and footer InlineError. Variants pass
 * their content (hero row, search section, chips, etc.) as
 * children — the wrapper is identical across variants.
 */
export function LookupShell({ search, gap = 'lg', children }: LookupShellProps) {
  const [, navigate] = useLocation();
  const gapClass = gap === 'md' ? 'gap-6' : 'gap-8';
  return (
    <SearchSeedProvider value={search.onSearchInput}>
      <div
        className="h-screen w-screen overflow-y-auto px-12 py-10"
        style={{ background: 'var(--bg-base)' }}
      >
        <div className={`mx-auto flex max-w-[1200px] flex-col ${gapClass}`}>
          <PageHeader eyebrow="Identify" onBack={() => navigate('/')} />
          {children}
        </div>
        <InlineError />
      </div>
    </SearchSeedProvider>
  );
}

export function PageHeader({ eyebrow, onBack }: PageHeaderProps) {
  return (
    <div className="flex items-center justify-between">
      <span
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--text-tertiary)' }}
      >
        {eyebrow}
      </span>
      <button
        type="button"
        onClick={onBack}
        className="font-mono text-[11px] uppercase tracking-[0.3em]"
        style={{ color: 'var(--text-tertiary)' }}
      >
        ↩ back
      </button>
    </div>
  );
}
