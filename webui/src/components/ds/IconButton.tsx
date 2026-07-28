/**
 * IconButton — square icon-only control for toolbars & headers. Visuals in ds-forms.css.
 * `label` is required (aria-label/title). Sizes: sm | md | lg.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** @default "sm" — use "lg" (44px) for primary mobile touch targets. */
  size?: 'sm' | 'md' | 'lg';
  /** Accessible label — required since there is no visible text. */
  label: string;
  /** The icon node (Lucide line icon, currentColor). */
  children: ReactNode;
}

/** murder IconButton — square icon-only control for toolbars & headers. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { size = 'sm', label, className, children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx('mds-iconbtn', size !== 'sm' && `mds-iconbtn--${size}`, className)}
      aria-label={label}
      title={label}
      {...rest}
    >
      {children}
    </button>
  );
});
