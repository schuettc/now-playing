import { useId } from 'react';

type SearchFieldSize = 'md' | 'lg';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onFocus?: () => void;
  onBlur?: () => void;
  size?: SearchFieldSize;
  placeholder?: string;
  autoFocus?: boolean;
}

const SIZE_PX: Record<SearchFieldSize, { height: number; text: number; radius: number }> = {
  md: { height: 64, text: 18, radius: 14 },
  lg: { height: 84, text: 24, radius: 16 },
};

export function searchFieldShowsClear(value: string): boolean {
  return value.length > 0;
}

/**
 * Touch-sized search input — leading `⌕` glyph + clear button
 * when the value is non-empty. Two sizes (md 64 / lg 84 px).
 *
 * Spec: docs/features/confirmed-fingerprint-coverage/design-output/
 * README.md § "Component vocabulary" → SearchField.
 */
export function SearchField({
  value, onChange, onFocus, onBlur, size = 'md', placeholder, autoFocus,
}: Props) {
  const id = useId();
  const dims = SIZE_PX[size];
  const showsClear = searchFieldShowsClear(value);
  return (
    <div
      data-testid="search-field"
      className="relative flex w-full items-center"
      style={{
        height: dims.height,
        borderRadius: dims.radius,
        background: 'rgba(255,255,255,0.06)',
        border: '1px solid var(--text-hairline)',
      }}
    >
      <label htmlFor={id} className="sr-only">Search</label>
      <span
        aria-hidden="true"
        className="absolute left-5 font-mono"
        style={{ color: 'var(--text-tertiary)', fontSize: dims.text * 0.9 }}
      >
        ⌕
      </span>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={onFocus}
        onBlur={onBlur}
        placeholder={placeholder}
        autoFocus={autoFocus}
        className="h-full w-full bg-transparent pl-14 pr-14 outline-none placeholder:opacity-50"
        style={{ color: 'var(--text-primary)', fontSize: dims.text }}
      />
      {showsClear && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange('')}
          className="absolute right-3 flex h-9 w-9 items-center justify-center rounded-full"
          style={{
            background: 'rgba(255,255,255,0.08)',
            color: 'var(--text-secondary)',
          }}
        >
          ×
        </button>
      )}
    </div>
  );
}
