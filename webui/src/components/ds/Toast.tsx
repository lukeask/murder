/**
 * Toast — a transient status line. A tone-colored left rail carries the signal; the body stays
 * calm. Use for crow lifecycle events ("crow spawned", "ticket failed"). A glyph + colored rail is
 * enough — no icons required. Ported from the DS bundle (feedback/Toast).
 *
 * Props use `Omit<…, 'title'>` (as Dialog does) so `title` can be a ReactNode rather than the DOM
 * `string`.
 *
 * Follows the Dialog exemplar: plain typed FC, props from the bundle `.d.ts`, `className` merged via
 * {@link cx}, `...rest` spread onto the root. All visuals live in ds-feedback.css (`.mds-toast*`).
 */

import { type HTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

/** Tone names — drive the left rail + glyph color. */
export type ToastTone =
  | 'running'
  | 'done'
  | 'failed'
  | 'archived'
  | 'pending'
  | 'success'
  | 'error'
  | 'info'
  | 'warning'
  | 'neutral';

export interface ToastProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** Tone — drives the left rail + glyph color. @default "neutral" */
  tone?: ToastTone;
  /** Primary line. */
  title?: ReactNode;
  /** Secondary muted line. */
  desc?: ReactNode;
  /** Override the leading glyph. */
  glyph?: ReactNode;
  /** Dismiss handler — shows the × button. */
  onClose?: () => void;
  children?: ReactNode;
}

/**
 * Default leading glyphs per tone. Text glyphs, intentionally NOT emoji — they render in the mono
 * face alongside the body and pick up the rail color.
 */
const GLYPHS: Record<ToastTone, string> = {
  success: '✓',
  done: '✓',
  running: '•',
  failed: '✕',
  error: '✕',
  info: '•',
  archived: '•',
  warning: '!',
  pending: '•',
  neutral: '·',
};

/** murder Toast — transient status line with a tone-colored left rail. */
export function Toast({
  tone = 'neutral',
  title,
  desc,
  glyph,
  onClose,
  className,
  children,
  ...rest
}: ToastProps): React.JSX.Element {
  return (
    <div className={cx('mds-toast', `mds-toast--${tone}`, className)} role="status" {...rest}>
      <span className="mds-toast__glyph">{glyph !== undefined ? glyph : GLYPHS[tone]}</span>
      <div className="mds-toast__body">
        <div className="mds-toast__title">{title !== undefined ? title : children}</div>
        {desc !== undefined ? <div className="mds-toast__desc">{desc}</div> : null}
      </div>
      {onClose !== undefined ? (
        <button
          type="button"
          className="mds-toast__close"
          aria-label="dismiss"
          onClick={onClose}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
