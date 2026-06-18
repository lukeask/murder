/**
 * Tooltip — a lightweight CSS-only hover/focus label. Wrap a trigger; the bubble appears above (or
 * below) on hover/focus with a caret triangle. No portals. Keep labels terse and lowercase. Ported
 * from the DS bundle (feedback/Tooltip).
 *
 * Follows the Dialog exemplar: plain typed FC, props from the bundle `.d.ts`, `className` merged via
 * {@link cx}, `...rest` spread onto the root. The root is `tabIndex={0}` so keyboard focus opens the
 * bubble (`:focus-within`). All visuals live in ds-feedback.css (`.mds-tip*`).
 */

import { type HTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface TooltipProps extends HTMLAttributes<HTMLSpanElement> {
  /** Tooltip text — keep it terse and lowercase. */
  label: ReactNode;
  /** @default "top" */
  placement?: 'top' | 'bottom';
  /** The trigger element. */
  children: ReactNode;
}

/** murder Tooltip — terse hover/focus label; CSS-driven, no portals. */
export function Tooltip({
  label,
  placement = 'top',
  className,
  children,
  ...rest
}: TooltipProps): React.JSX.Element {
  return (
    <span
      className={cx('mds-tip', placement === 'bottom' && 'mds-tip--bottom', className)}
      tabIndex={0}
      {...rest}
    >
      {children}
      <span className="mds-tip__bubble" role="tooltip">
        {label}
      </span>
    </span>
  );
}
