/**
 * Tabs — segmented control / panel switcher (underline or pill). Visuals in ds.css (`.mds-tabs*`).
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface TabItem {
  id: string;
  label: string;
  /** Optional leading line icon — e.g. for the mobile tab bar. */
  icon?: ReactNode;
  /** Optional count shown faint after the label. */
  count?: number | string;
}

export interface TabsProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onChange'> {
  tabs?: TabItem[];
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
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={t.id === value}
          className={cx(
            'mds-tab',
            t.id === value && 'mds-tab--active',
            t.icon !== undefined && 'mds-tab--stack',
          )}
          onClick={() => onChange?.(t.id)}
        >
          {t.icon !== undefined ? <span className="mds-tab__icon">{t.icon}</span> : null}
          {t.label}
          {t.count !== undefined ? <span className="mds-tab__count">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
