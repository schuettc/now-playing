import { AnimatePresence, motion } from 'framer-motion';
import { MOTION } from '@/lib/motion';

export type KeyAction = string | 'space' | 'backspace' | 'clear' | 'done';

/** Digit row + QWERTY letter rows (lowercase printable chars only). */
export const KEY_ROWS: string[][] = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
  ['z', 'x', 'c', 'v', 'b', 'n', 'm'],
];

/** Pure edit model — append / backspace at the END of the string only. */
export function applyKey(value: string, key: KeyAction): string {
  switch (key) {
    case 'space':
      return value + ' ';
    case 'backspace':
      return value.slice(0, -1);
    case 'clear':
      return '';
    case 'done':
      return value;
    default:
      return value + key;
  }
}

interface Props {
  visible: boolean;
  value: string;
  onChange: (v: string) => void;
  onDone: () => void;
  onClear: () => void;
}

const KEY_CLASS =
  'inline-flex min-h-[56px] min-w-[56px] flex-1 items-center justify-center ' +
  'select-none rounded-[10px] text-[20px] bg-[var(--text-hairline)] ' +
  'text-[var(--text-primary)]';

const TAP_MOTION = {
  whileTap: { scale: 0.98 },
  transition: { duration: MOTION.buttonPress, ease: 'easeOut' as const },
};

/**
 * On-screen touch keyboard for the no-physical-keyboard kiosk. Fixed to
 * the bottom of the viewport, animated in/out. Each key uses
 * `onPointerDown` + `preventDefault()` so the focused <input> keeps its
 * focus + caret while the value updates through React state.
 */
export function VirtualKeyboard({
  visible,
  value,
  onChange,
  onDone,
  onClear,
}: Props) {
  const tap = (action: KeyAction) => (e: React.PointerEvent) => {
    e.preventDefault();
    if (action === 'done') onDone();
    else if (action === 'clear') onClear();
    else onChange(applyKey(value, action));
  };

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          data-testid="virtual-keyboard"
          className="fixed inset-x-0 bottom-0 z-50 flex flex-col gap-2 bg-[var(--bg-base)] p-3"
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ duration: MOTION.chipIn, ease: 'easeOut' }}
        >
          {KEY_ROWS.map((row, i) => (
            <div key={i} className="flex justify-center gap-2">
              {row.map((char) => (
                <motion.button
                  key={char}
                  type="button"
                  aria-label={char}
                  className={KEY_CLASS}
                  onPointerDown={tap(char)}
                  {...TAP_MOTION}
                >
                  {char}
                </motion.button>
              ))}
            </div>
          ))}
          <div className="flex justify-center gap-2">
            <motion.button
              type="button"
              aria-label="Clear"
              className={KEY_CLASS}
              onPointerDown={tap('clear')}
              {...TAP_MOTION}
            >
              ×
            </motion.button>
            <motion.button
              type="button"
              aria-label="Space"
              className={`${KEY_CLASS} flex-[4]`}
              onPointerDown={tap('space')}
              {...TAP_MOTION}
            >
              space
            </motion.button>
            <motion.button
              type="button"
              aria-label="Backspace"
              className={KEY_CLASS}
              onPointerDown={tap('backspace')}
              {...TAP_MOTION}
            >
              ⌫
            </motion.button>
            <motion.button
              type="button"
              aria-label="Done"
              className={`${KEY_CLASS} flex-[2] bg-[var(--dot-ok)] text-[var(--bg-base-deep)] font-semibold`}
              onPointerDown={tap('done')}
              {...TAP_MOTION}
            >
              Done
            </motion.button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
