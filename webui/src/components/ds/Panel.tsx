/**
 * Panel — DS cockpit container (`.mds-panel`): titled, bordered, optional count + active + flush.
 * Visuals in ds.css.
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Panel title rendered on the top edge (e.g. "Plans", "Tickets"). */
  title?: ReactNode;
  /** Item count shown as a small pill badge at the right of the header. */
  count?: number | string | null;
  /** Focused/active region — 2px green border + green title. */
  active?: boolean;
  /** Remove body padding (for flush list rows). */
  flush?: boolean;
  /** Header action nodes (IconButtons) on the right. */
  actions?: ReactNode;
  children?: ReactNode;
}

/** murder Panel — the cockpit container: titled, bordered, count + active. */
export function Panel({
  title,
  count,
  active = false,
  flush = false,
  actions,
  className,
  children,
  ...rest
}: PanelProps): React.JSX.Element {
  return (
    <section
      className={cx(
        'mds-panel',
        active && 'mds-panel--active',
        flush && 'mds-panel--flush',
        className,
      )}
      {...rest}
    >
      {title !== undefined ? (
        <header className="mds-panel__head">
          <span className="mds-panel__title">{title}</span>
          {count !== undefined && count !== null ? (
            <span className="mds-panel__count">{count}</span>
          ) : null}
          <span className="mds-panel__spacer" />
          {actions !== undefined ? <span className="mds-panel__actions">{actions}</span> : null}
        </header>
      ) : null}
      <div className="mds-panel__body">{children}</div>
    </section>
  );
}
