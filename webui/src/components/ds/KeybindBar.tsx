/**
 * KeybindBar — the sticky bottom hint bar: chord chips + verb-noun, with a real help button at the
 * far right (icon + label) that opens a sheet. Wraps by default; `scroll` switches to single-line
 * horizontal scroll (hidden scrollbar) for mobile. Ported from the DS bundle following the Tabs
 * exemplar.
 *
 *  - Props derived from the bundle `.d.ts`. Plain typed FC; `className` merged via {@link cx};
 *    `...rest` spread onto the root <div>. Visuals live in ds-navigation.css (`.mds-keybar*`).
 *  - `help` is `string | null`: defaults to `"help"`; pass `null` to hide the button. We guard the
 *    `null` (explicit hide) vs the default precisely.
 */

import type { HTMLAttributes } from 'react';
import { cx } from './cx.js';
import { Icon } from './Icon.js';

export interface KeybindHint {
  chord: string;
  desc: string;
}

export interface KeybindBarProps extends HTMLAttributes<HTMLDivElement> {
  /** Chord + verb-noun hints, in order. */
  hints?: KeybindHint[];
  /** Help button label (icon + this text). Pass null to hide. @default "help" */
  help?: string | null;
  /** Fired when the help button is pressed (opens a help sheet). */
  onHelp?: () => void;
  /** @deprecated alias for onHelp. */
  onCommand?: () => void;
  /** Single-line horizontal scroll instead of wrapping (mobile). */
  scroll?: boolean;
}

/** murder KeybindBar — sticky bottom hint bar of chord chips + a help button. */
export function KeybindBar({
  hints = [],
  help = 'help',
  onHelp,
  onCommand,
  scroll = false,
  className,
  ...rest
}: KeybindBarProps): React.JSX.Element {
  const onHelpClick = onHelp ?? onCommand;
  return (
    <div className={cx('mds-keybar', scroll && 'mds-keybar--scroll', className)} {...rest}>
      {hints.map((h, i) => (
        <span className="mds-keybar__hint" key={i}>
          <span className="mds-keybar__chord">{h.chord}</span>
          <span className="mds-keybar__desc">{h.desc}</span>
        </span>
      ))}
      <span className="mds-keybar__spacer" />
      {help !== null ? (
        <button type="button" className="mds-keybar__help" onClick={onHelpClick}>
          <Icon name="help" size={16} />
          {help}
        </button>
      ) : null}
    </div>
  );
}
