/**
 * Tag — quiet inline token (model, label, target). Quieter than Badge; optional leading `dot`.
 * Visuals in ds-data.css (`.mds-tag*`).
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface TagProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'tone'> {
  /** Color accent. @default "neutral" */
  tone?: 'neutral' | 'accent' | 'brand';
  /** Show a leading dot in the current color. */
  dot?: boolean;
  children?: ReactNode;
}

/** murder Tag — quiet inline token (model, label, target). */
export function Tag({
  tone = 'neutral',
  dot = false,
  className,
  children,
  ...rest
}: TagProps): React.JSX.Element {
  return (
    <span className={cx('mds-tag', tone !== 'neutral' && `mds-tag--${tone}`, className)} {...rest}>
      {dot ? <span className="mds-tag__dot" /> : null}
      {children}
    </span>
  );
}
