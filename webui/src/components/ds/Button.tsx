/**
 * Button — the primary action control. Ported from the DS bundle (forms/Button).
 *
 * EXEMPLAR — Phase B forms components copy this shape:
 *  - Props derived from the bundle `.d.ts` (here: extends ButtonHTMLAttributes).
 *  - `React.forwardRef` because the bundle's element is a focusable control (button). forwardRef is
 *    used for inputs/buttons; plain FC otherwise (see Panel).
 *  - `className` merged onto the base class via the shared {@link cx} helper.
 *  - `...rest` spread onto the root element.
 *  - Visuals live entirely in ds.css (`.mds-btn*`); this file is structure + class composition only.
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
  /** Keybind chord shown as a chip on the right, e.g. "C-s". */
  keyHint?: string;
  /** Optional leading icon node (Lucide line icon, 16px). */
  leadingIcon?: ReactNode;
  /** Optional trailing icon node. */
  trailingIcon?: ReactNode;
  children?: ReactNode;
}

/** murder Button — the primary action control. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    block = false,
    keyHint,
    leadingIcon,
    trailingIcon,
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
      {leadingIcon}
      {children}
      {trailingIcon}
      {keyHint !== undefined ? <span className="mds-btn__key">{keyHint}</span> : null}
    </button>
  );
});
