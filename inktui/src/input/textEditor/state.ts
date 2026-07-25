/** Durable, terminal-independent state for a text editor. */
export interface TextEditorState {
  readonly text: string;
  /** UTF-16 offset into `text`; public operations keep this at a topology boundary. */
  readonly cursor: number;
  /** The column captured by repeated visual Up/Down movement. */
  readonly desiredVisualColumn: number | null;
}

export interface CursorTopology {
  normalizeCursor(text: string, cursor: number, bias: 'backward' | 'forward' | 'nearest'): number;
}

export function editorAtStart(text = ''): TextEditorState {
  return { text, cursor: 0, desiredVisualColumn: null };
}

export function editorAtEnd(text = ''): TextEditorState {
  return { text, cursor: text.length, desiredVisualColumn: null };
}

/** Clamp a foreign/restored state without retaining any geometry in it. */
export function normalizeEditorState(
  state: TextEditorState,
  topology: CursorTopology,
): TextEditorState {
  const cursor = topology.normalizeCursor(state.text, state.cursor, 'nearest');
  return cursor === state.cursor ? state : { ...state, cursor };
}

export function withCursor(state: TextEditorState, cursor: number): TextEditorState {
  return { text: state.text, cursor, desiredVisualColumn: null };
}
