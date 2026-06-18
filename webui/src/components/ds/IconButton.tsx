/**
 * IconButton — square, icon-only control for toolbars & headers. Ported from the DS bundle
 * (forms/IconButton). Mirrors the Button exemplar: props from the bundle `.d.ts`, `React.forwardRef`
 * (focusable <button>), `className` merged via {@link cx}, `...rest` spread, visuals in ds-forms.css.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** @default "sm" — use "lg" (44px) for primary mobile touch targets. */
  size?: 'sm' | 'md' | 'lg';
  /** Show the active/selected state (accent icon, raised surface). */
  active?: boolean;
  /** Draw a resting border (for standalone toolbar buttons). */
  bordered?: boolean;
  /** Accessible label — required since there is no visible text. */
  label: string;
  /** The icon node (Lucide line icon, currentColor). */
  children: ReactNode;
}

/** murder IconButton — square icon-only control for toolbars & headers. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { size = 'sm', active = false, bordered = false, label, className, children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        'mds-iconbtn',
        size !== 'sm' && `mds-iconbtn--${size}`,
        active && 'mds-iconbtn--active',
        bordered && 'mds-iconbtn--bordered',
        className,
      )}
      aria-label={label}
      title={label}
      {...rest}
    >
      {children}
    </button>
  );
});
