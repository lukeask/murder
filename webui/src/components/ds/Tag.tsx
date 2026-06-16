/**
 * Tag — a quiet inline token (model name, label, target). Optional leading dot and a removable ×.
 * Quieter than Badge — for metadata, not status. Ported from the DS bundle (data/Tag); follows the
 * Panel exemplar. Visuals live in ds-data.css (`.mds-tag*`).
 *
 * Remove ×: kept as the literal "×" glyph (matching the bundle's typographic 12px ×), NOT the shared
 * Icon `x` stroke SVG — the bundle styles it as text, so swapping in an SVG would change the look.
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface TagProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'tone'> {
  /** Color accent. @default "neutral" */
  tone?: 'neutral' | 'accent' | 'brand';
  /** Show a leading dot in the current color. */
  dot?: boolean;
  /** When provided, renders a × remove button and calls this on click. */
  onRemove?: () => void;
  children?: ReactNode;
}

/** murder Tag — quiet inline token (model, label, target); optional remove ×. */
export function Tag({
  tone = 'neutral',
  dot = false,
  onRemove,
  className,
  children,
  ...rest
}: TagProps): React.JSX.Element {
  return (
    <span className={cx('mds-tag', tone !== 'neutral' && `mds-tag--${tone}`, className)} {...rest}>
      {dot ? <span className="mds-tag__dot" /> : null}
      {children}
      {onRemove !== undefined ? (
        <button type="button" className="mds-tag__x" aria-label="remove" onClick={onRemove}>
          ×
        </button>
      ) : null}
    </span>
  );
}
