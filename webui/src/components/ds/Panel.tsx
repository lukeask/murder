/**
 * Panel — DS cockpit container (`.mds-panel`): titled, bordered, optional count + active + flush.
 * Visuals in ds.css. Body scrollports get subtle more-above/below edge fades.
 */

import { useRef, type HTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { scrollEdgesClassName, useScrollEdges } from '../../useScrollEdges.js';
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
  /** Click the header chrome (not actions) to claim rail keyboard focus. */
  onHeaderClick?: (e: MouseEvent<HTMLElement>) => void;
  children?: ReactNode;
}

/** murder Panel — the cockpit container: titled, bordered, count + active. */
export function Panel({
  title,
  count,
  active = false,
  flush = false,
  actions,
  onHeaderClick,
  className,
  children,
  ...rest
}: PanelProps): React.JSX.Element {
  const bodyRef = useRef<HTMLDivElement>(null);
  const edges = useScrollEdges(bodyRef);

  return (
    <section
      className={cx(
        'mds-panel',
        active && 'mds-panel--active',
        flush && 'mds-panel--flush',
        className,
      )}
      data-focused={active ? 'true' : undefined}
      {...rest}
    >
      {title !== undefined ? (
        <header
          className={cx('mds-panel__head', onHeaderClick !== undefined && 'mds-panel__head--focusable')}
          onClick={onHeaderClick}
        >
          <span className="mds-panel__title">{title}</span>
          {count !== undefined && count !== null ? (
            <span className="mds-panel__count">{count}</span>
          ) : null}
          <span className="mds-panel__spacer" />
          {actions !== undefined ? (
            <span
              className="mds-panel__actions"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {actions}
            </span>
          ) : null}
        </header>
      ) : null}
      <div ref={bodyRef} className={cx('mds-panel__body', scrollEdgesClassName(edges))}>
        {children}
      </div>
    </section>
  );
}
