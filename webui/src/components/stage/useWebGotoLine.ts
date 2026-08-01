/**
 * Browser port of ui-core useGotoLine: `g` then digits jumps live; Esc/Enter/g ends capture.
 * Listens on `window` while `enabled`, ignoring keystrokes that target form fields.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { reduceGoto } from '@murder/ui-core/hooks/useGotoLine.js';

export interface WebGotoLine {
  /** Digits captured so far (`''` right after `g`), or `null` when idle. */
  readonly pending: string | null;
  readonly clear: () => void;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}

export function useWebGotoLine(
  jump: (line: number) => void,
  enabled: boolean = true,
): WebGotoLine {
  const [pending, setPending] = useState<string | null>(null);
  const pendingRef = useRef<string | null>(null);
  const jumpRef = useRef(jump);
  jumpRef.current = jump;

  const clear = useCallback(() => {
    pendingRef.current = null;
    setPending(null);
  }, []);

  useEffect(() => {
    if (!enabled) {
      pendingRef.current = null;
      setPending(null);
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isEditableTarget(event.target)) return;

      const key = event.key;
      let intent: string | null = null;
      if (pendingRef.current === null) {
        if (key === 'g') intent = 'goto.start';
        else return;
      } else if (key >= '0' && key <= '9') {
        intent = `goto.digit.${key}`;
      } else if (key === 'g' || key === 'Escape' || key === 'Enter') {
        intent = 'goto.end';
      } else {
        // Any other key ends capture (TUI clear-on-other-intent).
        pendingRef.current = null;
        setPending(null);
        return;
      }

      const step = reduceGoto(pendingRef.current, intent);
      if (step === null) return;
      event.preventDefault();
      pendingRef.current = step.pending;
      setPending(step.pending);
      if (step.jumpTo !== null) jumpRef.current(step.jumpTo);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled]);

  return useMemo(() => ({ pending, clear }), [pending, clear]);
}
