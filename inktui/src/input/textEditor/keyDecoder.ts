import type { EditorCommand } from './commands.js';

/** Structural subset of Ink's normalized key event; keeping it local avoids a framework dependency. */
export interface EditorKey {
  readonly ctrl?: boolean;
  readonly meta?: boolean;
  readonly shift?: boolean;
  readonly return?: boolean;
  readonly tab?: boolean;
  readonly escape?: boolean;
  readonly backspace?: boolean;
  readonly delete?: boolean;
  readonly leftArrow?: boolean;
  readonly rightArrow?: boolean;
  readonly upArrow?: boolean;
  readonly downArrow?: boolean;
  readonly home?: boolean;
  readonly end?: boolean;
}

export interface EditorKeyPolicy {
  readonly newline: 'never' | 'enter' | 'shift-enter';
  readonly homeEnd: 'logical-line' | 'buffer';
  readonly ctrlLineMotion: boolean;
}

export const singleLineEditorPolicy: EditorKeyPolicy = {
  newline: 'never',
  homeEnd: 'logical-line',
  ctrlLineMotion: true,
};

export const multilineEditorPolicy: EditorKeyPolicy = {
  newline: 'shift-enter',
  homeEnd: 'logical-line',
  ctrlLineMotion: true,
};

export function decodeEditorKey(
  input: string,
  key: EditorKey,
  policy: EditorKeyPolicy,
): EditorCommand | null {
  if (key.escape || key.tab) return null;
  if (key.leftArrow) return { type: 'moveLeft' };
  if (key.rightArrow) return { type: 'moveRight' };
  if (key.upArrow) return { type: 'moveVisualUp' };
  if (key.downArrow) return { type: 'moveVisualDown' };
  if (key.backspace) return { type: 'backspace' };
  if (key.delete) return { type: 'deleteForward' };
  if (key.home) return { type: policy.homeEnd === 'buffer' ? 'moveBufferStart' : 'moveLineStart' };
  if (key.end) return { type: policy.homeEnd === 'buffer' ? 'moveBufferEnd' : 'moveLineEnd' };
  if (key.return) {
    const allows =
      policy.newline === 'enter' || (policy.newline === 'shift-enter' && key.shift === true);
    return allows ? { type: 'insertNewline' } : null;
  }
  if (policy.ctrlLineMotion && key.ctrl === true && !key.meta) {
    if (input === 'a') return { type: 'moveLineStart' };
    if (input === 'e') return { type: 'moveLineEnd' };
  }
  if (input.length > 0 && key.ctrl !== true && key.meta !== true)
    return { type: 'insert', text: input.replace(/\t/g, '    ') };
  return null;
}
