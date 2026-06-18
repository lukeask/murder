/**
 * NavBar — the top cockpit bar: brand mark + nav items, with the active item brand-colored and
 * underlined. Scrolls horizontally (hidden scrollbar) when cramped, for mobile. Ported from the DS
 * bundle (navigation/NavBar) following the Tabs exemplar.
 *
 *  - Props derived from the bundle `.d.ts`. `Omit<…, 'onSelect'>` so the DS `onSelect(id)` signature
 *    replaces any DOM one.
 *  - Plain typed FC; `className` merged via {@link cx}; `...rest` spread onto the root <nav>.
 *  - Visuals live entirely in ds-navigation.css (`.mds-nav*`).
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface NavItem {
  id: string;
  label: string;
  /** Optional leading line icon (a React node). */
  icon?: ReactNode;
  /** Keyboard shortcut — surfaced in the command menu / help sheet, not rendered inline. */
  key?: string | number;
}

export interface NavBarProps extends Omit<HTMLAttributes<HTMLElement>, 'onSelect'> {
  /** Brand mark text. @default "murder" */
  brand?: string;
  /** Nav items (strings or {id,label,key}). */
  items?: Array<string | NavItem>;
  /** Active item id. */
  active?: string;
  /** Selection callback (id). */
  onSelect?: (id: string) => void;
  /** Right-aligned slot (usage, avatar, settings IconButton). */
  trailing?: ReactNode;
}

/** murder NavBar — top cockpit bar with brand + nav items (active item underlined). */
export function NavBar({
  brand = 'murder',
  items = [],
  active,
  onSelect,
  trailing,
  className,
  ...rest
}: NavBarProps): React.JSX.Element {
  return (
    <nav className={cx('mds-nav', className)} {...rest}>
      <span className="mds-nav__brand">{brand}</span>
      <div className="mds-nav__items">
        {items.map((it) => {
          const id = typeof it === 'string' ? it : it.id;
          const label = typeof it === 'string' ? it : it.label;
          const icon = typeof it === 'string' ? undefined : it.icon;
          return (
            <button
              key={id}
              type="button"
              className={cx('mds-nav__item', id === active && 'mds-nav__item--active')}
              onClick={() => onSelect?.(id)}
            >
              {icon !== undefined ? icon : null}
              {label}
            </button>
          );
        })}
      </div>
      <span className="mds-nav__spacer" />
      {trailing !== undefined ? <span className="mds-nav__trail">{trailing}</span> : null}
    </nav>
  );
}
