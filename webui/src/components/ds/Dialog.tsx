/**
 * Dialog — centered modal sheet with a scrim; docks to a bottom sheet on narrow screens. Closes via
 * the × button, scrim click, and Escape. Ported from the DS bundle (feedback/Dialog).
 *
 * EXEMPLAR — Phase B feedback components copy this shape:
 *  - Props derived from the bundle `.d.ts` (note `Omit<…, 'title'>` so `title` can be a ReactNode).
 *  - Plain typed FC returning `JSX.Element | null` (renders nothing when `open` is false).
 *  - `className` merged onto `.mds-dialog` via {@link cx}; `...rest` spread onto the dialog element.
 *  - The Escape-to-close effect is component behavior; all visuals live in ds.css (`.mds-scrim`,
 *    `.mds-dialog*`).
 */

import { useEffect, type HTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface DialogProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** Controls visibility. @default true */
  open?: boolean;
  /** Header title; omit for a chromeless sheet. */
  title?: ReactNode;
  /** Close handler — wired to the × button, scrim click, and Escape. */
  onClose?: () => void;
  /** Footer action row (Buttons), right-aligned. */
  footer?: ReactNode;
  children?: ReactNode;
}

/** murder Dialog — centered modal; docks to a bottom sheet on narrow screens. */
export function Dialog({
  open = true,
  title,
  onClose,
  footer,
  className,
  children,
  ...rest
}: DialogProps): React.JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="mds-scrim" onClick={onClose}>
      <div
        className={cx('mds-dialog', className)}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        {...rest}
      >
        {title !== undefined ? (
          <div className="mds-dialog__head">
            <span className="mds-dialog__title">{title}</span>
            {onClose !== undefined ? (
              <button
                type="button"
                className="mds-dialog__close"
                aria-label="close"
                onClick={onClose}
              >
                ×
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="mds-dialog__body">{children}</div>
        {footer !== undefined ? <div className="mds-dialog__foot">{footer}</div> : null}
      </div>
    </div>
  );
}
