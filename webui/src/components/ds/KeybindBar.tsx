/**
 * KeybindBar — sticky bottom hint bar of chord chips + verb-noun. Wraps by default; `scroll`
 * switches to single-line horizontal scroll. Visuals in ds-navigation.css (`.mds-keybar*`).
 */

import type { HTMLAttributes } from 'react';
import { cx } from './cx.js';

export interface KeybindHint {
  chord: string;
  desc: string;
}

export interface KeybindBarProps extends HTMLAttributes<HTMLDivElement> {
  /** Chord + verb-noun hints, in order. */
  hints?: readonly KeybindHint[];
  /** Single-line horizontal scroll instead of wrapping (mobile). */
  scroll?: boolean;
}

/** murder KeybindBar — sticky bottom hint bar of chord chips. */
export function KeybindBar({
  hints = [],
  scroll = false,
  className,
  ...rest
}: KeybindBarProps): React.JSX.Element {
  return (
    <div className={cx('mds-keybar', scroll && 'mds-keybar--scroll', className)} {...rest}>
      {hints.map((h, i) => (
        <span className="mds-keybar__hint" key={i}>
          <span className="mds-keybar__chord">{h.chord}</span>
          <span className="mds-keybar__desc">{h.desc}</span>
        </span>
      ))}
      <span className="mds-keybar__spacer" />
    </div>
  );
}
