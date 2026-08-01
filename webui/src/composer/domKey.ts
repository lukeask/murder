/**
 * Map a DOM keyboard event onto ui-core's renderer-neutral {@link Key} + printable `input`.
 */

import type { Key } from '@murder/ui-core/input/keymap.js';

export type DomKeyEvent = {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
  readonly shiftKey: boolean;
};

/** Build the Key flags + printable input string the vim reducer / chat handler expect. */
export function keyFromDomEvent(e: DomKeyEvent): { readonly input: string; readonly key: Key } {
  const key: Key = {
    ctrl: e.ctrlKey,
    meta: e.metaKey || e.altKey,
    super: false,
    hyper: false,
    capsLock: false,
    numLock: false,
    shift: e.shiftKey,
    escape: e.key === 'Escape',
    leftArrow: e.key === 'ArrowLeft',
    rightArrow: e.key === 'ArrowRight',
    upArrow: e.key === 'ArrowUp',
    downArrow: e.key === 'ArrowDown',
    return: e.key === 'Enter',
    backspace: e.key === 'Backspace',
    delete: e.key === 'Delete',
    tab: e.key === 'Tab',
    pageDown: e.key === 'PageDown',
    pageUp: e.key === 'PageUp',
    home: e.key === 'Home',
    end: e.key === 'End',
  };
  // Printable single-char (incl. space); named keys yield empty input.
  const input =
    e.key.length === 1 && !e.ctrlKey && !e.metaKey
      ? e.key
      : e.key === 'Enter' || e.key === 'Escape' || e.key.startsWith('Arrow')
        ? ''
        : '';
  return { input, key };
}

/** Approximate monospace content width in cells for visualUp/visualDown. */
export function estimateContentWidth(el: HTMLTextAreaElement | null): number {
  if (el === null) return 80;
  const style = getComputedStyle(el);
  const fontSize = Number.parseFloat(style.fontSize) || 14;
  const ch = fontSize * 0.62;
  const padL = Number.parseFloat(style.paddingLeft) || 0;
  const padR = Number.parseFloat(style.paddingRight) || 0;
  const usable = Math.max(0, el.clientWidth - padL - padR);
  // Unmeasured / jsdom (clientWidth 0) must not collapse to 1 cell — that makes every draft wrap and
  // steals ArrowUp/Down from history recall. Fall back to a terminal-like default.
  if (usable < 8) return 80;
  return Math.max(1, Math.floor(usable / ch));
}
