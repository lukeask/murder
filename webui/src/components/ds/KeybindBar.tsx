/**
 * KeybindBar — sticky bottom hint ledger of chord chips + optional widget segments.
 * Visuals in ds-navigation.css (`.mds-keybar*`) — beam/ledger, not pill cluster.
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export interface KeybindHint {
  chord: string;
  desc: string;
}

export interface KeybindBarProps extends HTMLAttributes<HTMLDivElement> {
  /** Chord + verb-noun hints, in order. */
  hints?: readonly KeybindHint[];
  /** Optional leading segments (usage / workspace) before hints. */
  leading?: ReactNode;
  /** Single-line horizontal scroll instead of wrapping (mobile). */
  scroll?: boolean;
}

/** murder KeybindBar — sticky bottom hint bar of chord chips. */
export function KeybindBar({
  hints = [],
  leading,
  scroll = false,
  className,
  ...rest
}: KeybindBarProps): React.JSX.Element {
  return (
    <div className={cx('mds-keybar', scroll && 'mds-keybar--scroll', className)} {...rest}>
      {leading !== undefined && leading !== null ? (
        <span className="mds-keybar__leading">{leading}</span>
      ) : null}
      {hints.map((h, i) => (
        <span className="mds-keybar__hint" key={i}>
          <span className="mds-keybar__chord">{h.chord}</span>
          {h.desc !== '' ? <span className="mds-keybar__desc">{h.desc}</span> : null}
        </span>
      ))}
      <span className="mds-keybar__spacer" />
    </div>
  );
}
