import wrapAnsi from 'wrap-ansi';

export interface WrapTextOptions {
  /** Hard-wrap at column width (code/pre). Default false (soft word wrap). */
  readonly hard?: boolean;
  /** Split on spaces when possible. Default true; set false with hard for character breaks. */
  readonly wordWrap?: boolean;
}

/** Split `text` into terminal rows at most `columns` wide (ANSI-aware). */
export function wrapTextToRows(
  text: string,
  columns: number,
  options: WrapTextOptions = {},
): readonly string[] {
  if (columns < 1) {
    return [text];
  }
  const wrapped = wrapAnsi(text, columns, {
    hard: options.hard ?? false,
    wordWrap: options.wordWrap ?? true,
    trim: false,
  });
  if (wrapped === '') {
    return [''];
  }
  return wrapped.split('\n');
}
