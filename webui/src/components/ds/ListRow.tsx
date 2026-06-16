/**
 * ListRow — the dense, selectable cockpit row: optional star pin toggle, title, optional meta line,
 * trailing status/value. Selected = calm full-width fill + green accent rail. Ported from the DS
 * bundle (data/ListRow); follows the Panel exemplar (plain typed FC, `cx` merge, `...rest` spread).
 *
 * Polymorphic via `as` (default "div"). The star pin has 3 states: true (filled amber), false (faint
 * outline), undefined (no slot). With `onPinToggle` the slot is a nested <button> (stops row-click
 * propagation); without it, a decorative <span>.
 *
 * Star icon: uses the shared {@link Icon} `star` (overriding its fill for the on-state) rather than the
 * bundle's locally-inlined polygon glyph.
 */

import type { ElementType, HTMLAttributes, MouseEvent, ReactNode } from 'react';
import { cx } from './cx.js';
import { Icon } from './Icon.js';

export interface ListRowProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Primary row text (single line, truncates). */
  title?: ReactNode;
  /** Secondary meta line below the title (timestamps, char counts). */
  meta?: ReactNode;
  /** Trailing slot — usually a Badge/StatusDot or a value. */
  trailing?: ReactNode;
  /** Pin slot as a star icon. true = pinned (filled + amber); false = reserved-but-empty (faint outline). Omit for no slot. */
  starred?: boolean;
  /** When set, the pin becomes a real toggle button (stops row-click propagation). */
  onPinToggle?: (e: MouseEvent) => void;
  /** Selected/current row — calm full-width fill + green accent rail. */
  selected?: boolean;
  /** Element tag to render (e.g. "button", "a", "div"). @default "div" */
  as?: ElementType;
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
  as,
  className,
  children,
  ...rest
}: ListRowProps): React.JSX.Element {
  const Tag: ElementType = as ?? 'div';
  const starCls = cx('mds-row__star', starred === true && 'mds-row__star--on');
  const star = (
    <Icon name="star" size={16} fill={starred === true ? 'currentColor' : 'none'} />
  );

  return (
    <Tag className={cx('mds-row', selected && 'mds-row--selected', className)} {...rest}>
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
    </Tag>
  );
}
