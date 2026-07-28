/**
 * Badge — status/label chip. `tone` maps to crow states (+ semantic aliases); optional `dot`.
 * Visuals in ds-data.css (`.mds-badge*`).
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export type BadgeTone =
  | 'running'
  | 'done'
  | 'failed'
  | 'archived'
  | 'pending'
  | 'idle'
  | 'blocked'
  | 'success'
  | 'error'
  | 'info'
  | 'warning'
  | 'neutral';

export interface BadgeProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'tone'> {
  /** Intent; status tones map to crow states, semantic tones are aliases. @default "neutral" */
  tone?: BadgeTone;
  /** Show a leading status dot. */
  dot?: boolean;
  children?: ReactNode;
}

/** murder Badge — status/label chip; tones map to crow states. */
export function Badge({
  tone = 'neutral',
  dot = false,
  className,
  children,
  ...rest
}: BadgeProps): React.JSX.Element {
  return (
    <span className={cx('mds-badge', `mds-badge--${tone}`, className)} {...rest}>
      {dot ? <span className="mds-badge__dot" /> : null}
      {children}
    </span>
  );
}
