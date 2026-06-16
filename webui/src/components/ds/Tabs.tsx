/**
 * Tabs — segmented control / panel switcher (underline or pill); doubles as the mobile bottom panel
 * switcher. Ported from the DS bundle (navigation/Tabs).
 *
 * EXEMPLAR — Phase B navigation components copy this shape:
 *  - Props derived from the bundle `.d.ts`. Note the `Omit<…, 'onChange'>` so the DS `onChange(id)`
 *    signature replaces the DOM one.
 *  - Plain typed FC; `className` merged via {@link cx}; `...rest` spread onto the root <div>.
 *  - Visuals live entirely in ds.css (`.mds-tabs*` / `.mds-tab*`).
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface TabItem {
  id: string;
  label: string;
  /** Optional leading line icon (a React node) — e.g. for the mobile tab bar. */
  icon?: ReactNode;
  /** Optional count shown faint after the label. */
  count?: number | string;
}

export interface TabsProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onChange'> {
  /** Tabs as strings or {id,label,count}. */
  tabs?: Array<string | TabItem>;
  /** Active tab id. */
  value?: string;
  onChange?: (id: string) => void;
  /** @default "underline" */
  variant?: 'underline' | 'pill';
  /** Stretch tabs to fill width (mobile panel switcher). */
  full?: boolean;
}

/** murder Tabs — segmented control / panel switcher; underline or pill. */
export function Tabs({
  tabs = [],
  value,
  onChange,
  variant = 'underline',
  full = false,
  className,
  ...rest
}: TabsProps): React.JSX.Element {
  return (
    <div
      className={cx('mds-tabs', `mds-tabs--${variant}`, full && 'mds-tabs--full', className)}
      role="tablist"
      {...rest}
    >
      {tabs.map((t) => {
        const id = typeof t === 'string' ? t : t.id;
        const label = typeof t === 'string' ? t : t.label;
        const count = typeof t === 'string' ? undefined : t.count;
        const icon = typeof t === 'string' ? undefined : t.icon;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={id === value}
            className={cx(
              'mds-tab',
              id === value && 'mds-tab--active',
              icon !== undefined && 'mds-tab--stack',
            )}
            onClick={() => onChange?.(id)}
          >
            {icon !== undefined ? <span className="mds-tab__icon">{icon}</span> : null}
            {label}
            {count !== undefined ? <span className="mds-tab__count">{count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
