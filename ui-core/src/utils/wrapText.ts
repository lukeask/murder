import wrapAnsi from 'wrap-ansi';
import { terminalSafeText } from './terminalSafeText.js';

export interface WrapTextOptions {
  /** Hard-wrap at column width (code/pre). Default false (soft word wrap). */
  readonly hard?: boolean;
  /** Split on spaces when possible. Default true; set false with hard for character breaks. */
  readonly wordWrap?: boolean;
  /**
   * Sanitize controls/ANSI before wrapping. Default true.
   * Set false only when the caller already ran {@link terminalSafeText}.
   */
  readonly sanitize?: boolean;
}

/** Split `text` into terminal rows at most `columns` wide (ANSI-aware). */
export function wrapTextToRows(
  text: string,
  columns: number,
  options: WrapTextOptions = {},
): readonly string[] {
  const safe = options.sanitize === false ? text : terminalSafeText(text);
  if (columns < 1) {
    return [safe];
  }
  const wrapped = wrapAnsi(safe, columns, {
    hard: options.hard ?? false,
    wordWrap: options.wordWrap ?? true,
    trim: false,
  });
  if (wrapped === '') {
    return [''];
  }
  return wrapped.split('\n');
}

/** Clamp to a single terminal row of at most `columns` (hard cut). */
export function truncateToWidth(text: string, columns: number): string {
  const rows = wrapTextToRows(text, Math.max(1, columns), {
    hard: true,
    wordWrap: false,
  });
  return rows[0] ?? '';
}
