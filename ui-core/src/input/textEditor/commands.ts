export type EditorCommand =
  | { readonly type: 'insert'; readonly text: string }
  | { readonly type: 'backspace' }
  | { readonly type: 'deleteForward' }
  | { readonly type: 'moveLeft' }
  | { readonly type: 'moveRight' }
  | { readonly type: 'moveVisualUp' }
  | { readonly type: 'moveVisualDown' }
  | { readonly type: 'moveLineStart' }
  | { readonly type: 'moveLineEnd' }
  | { readonly type: 'moveBufferStart' }
  | { readonly type: 'moveBufferEnd' }
  | { readonly type: 'moveWordForward' }
  | { readonly type: 'moveWordBackward' }
  | { readonly type: 'moveWordEnd' }
  | { readonly type: 'insertNewline' };
