/**
 * ListRow — dense selectable cockpit row: optional star pin, title, meta, trailing.
 * Clickable rows are `div[role=button]` so nested pin/trailing buttons stay valid HTML.
 */

import type { HTMLAttributes, KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { cx } from './cx.js';
import { Icon } from './Icon.js';

export interface ListRowProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Primary row text (single line, truncates). */
  title?: ReactNode;
  /** Secondary meta line below the title (timestamps, char counts). */
  meta?: ReactNode;
  /** Trailing slot — usually a Badge/StatusDot or a value. */
  trailing?: ReactNode;
  /** Pin slot as a star icon. true = pinned; false = reserved-but-empty; omit for no slot. */
  starred?: boolean;
  /** When set, the pin becomes a real toggle button (stops row-click propagation). */
  onPinToggle?: (e: MouseEvent) => void;
  /** Selected/current row — calm full-width fill + green accent rail. */
  selected?: boolean;
  children?: ReactNode;
}

/** murder ListRow — dense, selectable cockpit row (pin icon, title, meta, status). */
export function ListRow({
  title,
  meta,
  trailing,
  starred,
  onPinToggle,
  selected = false,
  className,
  children,
  ...rest
}: ListRowProps): React.JSX.Element {
  const { onClick, onKeyDown, role, tabIndex, ...rowRest } = rest;
  const interactive = onClick !== undefined;
  const starCls = cx('mds-row__star', starred === true && 'mds-row__star--on');
  const star = (
    <Icon name="star" size={16} fill={starred === true ? 'currentColor' : 'none'} />
  );

  const onRowKeyDown = (e: KeyboardEvent<HTMLElement>): void => {
    onKeyDown?.(e);
    if (e.defaultPrevented || !interactive || onClick === undefined) {
      return;
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick(e as unknown as MouseEvent<HTMLElement>);
    }
  };

  return (
    <div
      className={cx('mds-row', selected && 'mds-row--selected', className)}
      role={interactive ? 'button' : role}
      tabIndex={interactive ? (tabIndex ?? 0) : tabIndex}
      onClick={onClick}
      onKeyDown={onRowKeyDown}
      {...rowRest}
    >
      {starred !== undefined ? (
        onPinToggle !== undefined ? (
          <button
            type="button"
            className={starCls}
            aria-pressed={starred}
            aria-label={starred ? 'unpin' : 'pin'}
            onClick={(e) => {
              e.stopPropagation();
              onPinToggle(e);
            }}
          >
            {star}
          </button>
        ) : (
          <span className={starCls} aria-hidden="true">
            {star}
          </span>
        )
      ) : null}
      <span className="mds-row__main">
        <span className="mds-row__title">{title !== undefined ? title : children}</span>
        {meta !== undefined ? <span className="mds-row__meta">{meta}</span> : null}
      </span>
      {trailing !== undefined ? <span className="mds-row__trail">{trailing}</span> : null}
    </div>
  );
}
