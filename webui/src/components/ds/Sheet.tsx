/**
 * Sheet — bottom-anchored mobile sheet with a grab handle, swipe-down dismiss, scrim click and
 * Escape. On wider viewports it renders as a centered card (same contract as {@link Dialog}).
 * Visuals in ds-sheet.css (`.mds-sheet*`).
 */

import { useEffect, useRef, useState, type HTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface SheetProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** Controls visibility. @default true */
  open?: boolean;
  /** Header title; omit for a chromeless sheet. */
  title?: ReactNode;
  /** Close handler — wired to swipe-down, scrim click, and Escape. */
  onClose?: () => void;
  /** Footer action row. */
  footer?: ReactNode;
  children?: ReactNode;
}

/** Fraction of the sheet height that must be dragged before release dismisses it. */
const DISMISS_RATIO = 0.35;

export function Sheet({
  open = true,
  title,
  onClose,
  footer,
  className,
  children,
  ...rest
}: SheetProps): React.JSX.Element | null {
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragStartY = useRef<number | null>(null);
  const [dragOffset, setDragOffset] = useState(0);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) {
      dragStartY.current = null;
      setDragOffset(0);
    }
  }, [open]);

  if (!open) return null;

  const onHandleTouchStart = (e: React.TouchEvent): void => {
    dragStartY.current = e.touches[0]?.clientY ?? null;
  };
  const onHandleTouchMove = (e: React.TouchEvent): void => {
    if (dragStartY.current === null) return;
    const y = e.touches[0]?.clientY;
    if (y === undefined) return;
    setDragOffset(Math.max(0, y - dragStartY.current));
  };
  const onHandleTouchEnd = (): void => {
    const height = sheetRef.current?.offsetHeight ?? 0;
    const shouldClose = height > 0 && dragOffset > height * DISMISS_RATIO;
    dragStartY.current = null;
    setDragOffset(0);
    if (shouldClose) onClose?.();
  };

  return (
    <div className="mds-scrim mds-sheet-scrim" onClick={onClose}>
      <div
        ref={sheetRef}
        className={cx('mds-sheet', dragOffset > 0 && 'mds-sheet--dragging', className)}
        role="dialog"
        aria-modal="true"
        style={dragOffset > 0 ? { transform: `translateY(${dragOffset}px)` } : undefined}
        onClick={(e) => e.stopPropagation()}
        {...rest}
      >
        <div
          className="mds-sheet__grab"
          onTouchStart={onHandleTouchStart}
          onTouchMove={onHandleTouchMove}
          onTouchEnd={onHandleTouchEnd}
        >
          <span className="mds-sheet__handle" />
        </div>
        {title !== undefined ? (
          <div className="mds-sheet__head">
            <span className="mds-sheet__title">{title}</span>
            {onClose !== undefined ? (
              <button type="button" className="mds-sheet__close" aria-label="close" onClick={onClose}>
                ×
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="mds-sheet__body">{children}</div>
        {footer !== undefined ? <div className="mds-sheet__foot">{footer}</div> : null}
      </div>
    </div>
  );
}
