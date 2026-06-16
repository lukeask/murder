/**
 * KeyHint — a keybind chord chip (e.g. "C-p" or ["C","p"]) + optional muted verb-noun description.
 * Chord is green by default (yellow/muted tones, or a boxed key-cap). Ported from the DS bundle
 * (data/KeyHint); follows the Panel exemplar. Visuals live in ds-data.css (`.mds-keyhint*`).
 */

import type { HTMLAttributes } from 'react';
import { cx } from './cx.js';

export interface KeyHintProps extends HTMLAttributes<HTMLSpanElement> {
  /** The chord, e.g. "C-p" or ["C","p"]. */
  chord: string | string[];
  /** Verb-noun description shown muted after the chord, e.g. "new plan". */
  desc?: string;
  /** Chord color. @default "green" */
  tone?: 'green' | 'yellow' | 'muted';
  /** Render the chord as a boxed key cap instead of bare colored text. */
  boxed?: boolean;
}

/** murder KeyHint — a keybind chord chip + optional muted description. */
export function KeyHint({
  chord,
  desc,
  tone = 'green',
  boxed = false,
  className,
  ...rest
}: KeyHintProps): React.JSX.Element {
  const text = Array.isArray(chord) ? chord.join('-') : chord;
  return (
    <span
      className={cx(
        'mds-keyhint',
        tone !== 'green' && `mds-keyhint--${tone}`,
        boxed && 'mds-keyhint--boxed',
        className,
      )}
      {...rest}
    >
      <span className="mds-keyhint__chord">{text}</span>
      {desc !== undefined ? <span className="mds-keyhint__desc">{desc}</span> : null}
    </span>
  );
}
