/**
 * j/k/Enter (+ optional letter actions) while a rail panel is keyboard-focused.
 * Bails when the event target is a form field so composer/settings stay usable.
 */

import { useEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import { panelFocusStore } from './panelFocus.js';

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.closest('[data-terminal-input="true"]') !== null) return true;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.isContentEditable;
}

export type PanelListKeyHandlers = {
  /** Panel currently owns rail keyboard focus. */
  readonly active: boolean;
  readonly itemCount: number;
  readonly cursor: number;
  readonly setCursor: Dispatch<SetStateAction<number>>;
  /** Enter / Return on the cursor row. */
  readonly onActivate?: () => void;
  /**
   * Plain letter actions (e.g. `f` star, `x` dismiss, `r` sample).
   * Return true when handled. Shift is ignored for letter matching (use uppercase in map if needed).
   */
  readonly onAction?: (key: string, event: KeyboardEvent) => boolean;
};

/** Wire document-level list navigation while `active` and not typing in a field. */
export function usePanelListKeys({
  active,
  itemCount,
  cursor,
  setCursor,
  onActivate,
  onAction,
}: PanelListKeyHandlers): void {
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  const countRef = useRef(itemCount);
  countRef.current = itemCount;
  const onActivateRef = useRef(onActivate);
  onActivateRef.current = onActivate;
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;

  useEffect(() => {
    if (!active) return;
    if (itemCount <= 0) {
      setCursor(0);
      return;
    }
    setCursor((c) => Math.min(Math.max(0, c), itemCount - 1));
  }, [active, itemCount, setCursor]);

  useEffect(() => {
    if (!active) return;

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.defaultPrevented || e.repeat) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;

      const count = countRef.current;
      const key = e.key;

      if (key === 'Escape') {
        e.preventDefault();
        panelFocusStore.getState().clear();
        return;
      }

      if (key === 'j' || key === 'ArrowDown') {
        if (count <= 0) return;
        e.preventDefault();
        setCursor((c) => Math.min(c + 1, count - 1));
        return;
      }
      if (key === 'k' || key === 'ArrowUp') {
        if (count <= 0) return;
        e.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
        return;
      }
      if (key === 'Enter') {
        if (onActivateRef.current === undefined) return;
        e.preventDefault();
        onActivateRef.current();
        return;
      }
      if (key.length === 1 && onActionRef.current !== undefined) {
        if (onActionRef.current(key, e)) {
          e.preventDefault();
        }
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [active, setCursor]);
}
