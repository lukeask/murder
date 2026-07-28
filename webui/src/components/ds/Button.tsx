/**
 * Button — primary action control. Visuals in ds.css (`.mds-btn*`).
 * Variants: primary | brand | secondary | ghost | danger. Sizes: sm | md | lg.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * Intent / emphasis.
   * - primary: green fill — the main affirmative action (spawn, save)
   * - brand:   coral fill — destructive-but-branded / the murder mark action
   * - secondary: outlined neutral (default)
   * - ghost:   text-only, lifts on hover
   * - danger:  red outline — destructive (kill crow, delete)
   * @default "secondary"
   */
  variant?: 'primary' | 'brand' | 'secondary' | 'ghost' | 'danger';
  /** @default "md" */
  size?: 'sm' | 'md' | 'lg';
  /** Stretch to fill the container width. */
  block?: boolean;
  children?: ReactNode;
}

/** murder Button — the primary action control. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    block = false,
    className,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        'mds-btn',
        `mds-btn--${variant}`,
        size !== 'md' && `mds-btn--${size}`,
        block && 'mds-btn--block',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
