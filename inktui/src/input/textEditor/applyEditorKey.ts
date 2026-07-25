import type { EditorTransition, EditorEnvironment } from './operations.js';
import { reduceEditor } from './operations.js';
import type { TextEditorState } from './state.js';
import { decodeEditorKey, type EditorKey, type EditorKeyPolicy } from './keyDecoder.js';

export function applyEditorKey(
  state: TextEditorState,
  input: string,
  key: EditorKey,
  options: { readonly policy: EditorKeyPolicy; readonly environment: EditorEnvironment },
): EditorTransition | null {
  const command = decodeEditorKey(input, key, options.policy);
  return command === null ? null : reduceEditor(state, command, options.environment);
}
