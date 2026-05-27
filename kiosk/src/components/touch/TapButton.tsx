import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { motion, type MotionProps } from 'framer-motion';
import { MOTION } from '@/lib/motion';

type TapButtonIntent = 'primary' | 'danger' | 'ghost' | 'default';
type TapButtonSize = 'standard' | 'large';

interface Props
  extends Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    'onAnimationStart' | 'onDragStart' | 'onDragEnd' | 'onDrag'
  > {
  intent?: TapButtonIntent;
  size?: TapButtonSize;
}

const INTENT_STYLES: Record<TapButtonIntent, string> = {
  primary:
    'bg-[var(--dot-ok)] text-[var(--bg-base-deep)] font-semibold tracking-tight ' +
    'hover:bg-[#8ed99c]',
  danger:
    'bg-transparent text-[var(--sem-danger)] border border-[var(--sem-danger)] ' +
    'font-semibold hover:bg-[rgba(226,123,111,0.08)]',
  ghost:
    'bg-transparent text-[var(--text-body)] border border-[var(--text-hairline)] ' +
    'hover:bg-[var(--text-hairline)]',
  default:
    'bg-[var(--text-hairline)] text-[var(--text-primary)] ' +
    'hover:bg-[rgba(255,255,255,0.12)]',
};

const SIZE_STYLES: Record<TapButtonSize, string> = {
  standard: 'min-h-[56px] px-6 text-[18px] rounded-[10px]',
  large: 'min-h-[72px] px-8 text-[22px] rounded-[12px]',
};

const MOTION_PROPS: MotionProps = {
  whileTap: { scale: 0.98 },
  transition: { duration: MOTION.buttonPress, ease: 'easeOut' },
};

/**
 * Single button primitive used across every confirm-first UX surface.
 * Four intents, two sizes (56px standard, 72px large per design).
 *
 * Spec: docs/features/confirmed-fingerprint-coverage/design-output/
 * README.md § "Component vocabulary" → TapButton.
 */
export const TapButton = forwardRef<HTMLButtonElement, Props>(function TapButton(
  { intent = 'default', size = 'standard', className = '', children, disabled, ...rest },
  ref,
) {
  const cls = [
    'inline-flex items-center justify-center select-none',
    'transition-colors duration-[140ms] ease-out',
    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--dot-wait)]',
    'disabled:opacity-50 disabled:cursor-not-allowed',
    SIZE_STYLES[size],
    INTENT_STYLES[intent],
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <motion.button
      ref={ref}
      className={cls}
      disabled={disabled}
      {...MOTION_PROPS}
      {...(rest as MotionProps & ButtonHTMLAttributes<HTMLButtonElement>)}
    >
      {children}
    </motion.button>
  );
});
