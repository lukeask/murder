/**
 * Badge — small status/label chip. `tone` maps to crow states (running/done/failed/archived/pending/
 * idle) with semantic aliases (success/error/info/warning/neutral); `variant` is soft (tinted wash),
 * subtle (outline), or solid (filled). Ported from the DS bundle (data/Badge); follows the Panel
 * exemplar. Visuals live in ds-data.css (`.mds-badge*`).
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
  | 'success'
  | 'error'
  | 'info'
  | 'warning'
  | 'neutral';

export interface BadgeProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'tone'> {
  /** Intent; status tones map to crow states, semantic tones are aliases. @default "neutral" */
  tone?: BadgeTone;
  /** soft (tinted wash), subtle (outline), or solid (filled). @default "soft" */
  variant?: 'soft' | 'subtle' | 'solid';
  /** Show a leading status dot. */
  dot?: boolean;
  children?: ReactNode;
}

/** murder Badge — status/label chip; tones map to crow states. */
export function Badge({
  tone = 'neutral',
  variant = 'soft',
  dot = false,
  className,
  children,
  ...rest
}: BadgeProps): React.JSX.Element {
  return (
    <span
      className={cx(
        'mds-badge',
        `mds-badge--${tone}`,
        variant === 'subtle' && 'mds-badge--subtle',
        variant === 'solid' && 'mds-badge--solid',
        className,
      )}
      {...rest}
    >
      {dot ? <span className="mds-badge__dot" /> : null}
      {children}
    </span>
  );
}
