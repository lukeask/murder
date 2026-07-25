/**
 * Compatibility facade for the chat editor. Generic editing and wrapping live in `textEditor`; this
 * module only composes the chat image-span topology/projection and preserves the public chat API.
 */
import { chatProjection } from './chat/chatProjection.js';
import { makeSpan } from './chat/chatSpans.js';
import { chatTopology, snapChatCursor } from './chat/chatTopology.js';
import type { EditorCommand } from './textEditor/commands.js';
import { layoutEditor } from './textEditor/layout.js';
import { reduceEditor } from './textEditor/operations.js';
import type { TextEditorState } from './textEditor/state.js';

/** `desiredVisualColumn` is optional only for source compatibility with persisted pre-refactor drafts. */
export interface BufferState {
  readonly text: string;
  readonly cursor: number;
  readonly desiredVisualColumn?: number | null;
}

export const EMPTY_BUFFER: BufferState = { text: '', cursor: 0 };

const environment = (width = 1) => ({ width, topology: chatTopology, projection: chatProjection });
const editor = (state: BufferState): TextEditorState => ({
  text: state.text,
  cursor: state.cursor,
  desiredVisualColumn: state.desiredVisualColumn ?? null,
});
const hasDesired = (state: BufferState): boolean => Object.hasOwn(state, 'desiredVisualColumn');
const buffer = (state: TextEditorState, retainNull = false): BufferState =>
  state.desiredVisualColumn === null && !retainNull
    ? { text: state.text, cursor: state.cursor }
    : state;

function transition(state: BufferState, command: EditorCommand, width = 1) {
  return reduceEditor(editor(state), command, environment(width));
}

export function snapCursor(text: string, offset: number): number {
  return snapChatCursor(text, offset);
}

export function insert(state: BufferState, text: string): BufferState {
  return buffer(transition(state, { type: 'insert', text }).state, hasDesired(state));
}

export function insertImageSpan(state: BufferState, id: string): BufferState {
  return insert(state, makeSpan(id));
}

export function backspace(state: BufferState): { state: BufferState; removedId: string | null } {
  const result = transition(state, { type: 'backspace' });
  const effect = result.effects.find((candidate) => candidate.type === 'removedAtom');
  return {
    state: buffer(result.state, hasDesired(state)),
    removedId: effect?.type === 'removedAtom' ? effect.id : null,
  };
}

export function deleteForward(state: BufferState): {
  state: BufferState;
  removedId: string | null;
} {
  const result = transition(state, { type: 'deleteForward' });
  const effect = result.effects.find((candidate) => candidate.type === 'removedAtom');
  return {
    state: buffer(result.state, hasDesired(state)),
    removedId: effect?.type === 'removedAtom' ? effect.id : null,
  };
}

export function moveLeft(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveLeft' }).state, hasDesired(state));
}
export function moveRight(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveRight' }).state, hasDesired(state));
}
export function moveLineStart(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveLineStart' }).state, hasDesired(state));
}
export function moveLineEnd(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveLineEnd' }).state, hasDesired(state));
}
export function moveBufferStart(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveBufferStart' }).state, hasDesired(state));
}
export function moveBufferEnd(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveBufferEnd' }).state, hasDesired(state));
}
export function moveWordForward(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveWordForward' }).state, hasDesired(state));
}
export function moveWordBackward(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveWordBackward' }).state, hasDesired(state));
}
export function moveWordEnd(state: BufferState): BufferState {
  return buffer(transition(state, { type: 'moveWordEnd' }).state, hasDesired(state));
}

export interface VisualRow {
  readonly text: string;
  readonly startBufferOffset: number;
}
export interface VisualLayout {
  readonly rows: readonly VisualRow[];
  readonly cursorRow: number;
  readonly cursorCol: number;
}

export function layout(state: BufferState, width: number): VisualLayout {
  const result = layoutEditor(editor(state), width, chatProjection);
  return {
    rows: result.rows.map((row) => ({
      text: row.atoms.map((atom) => atom.text).join(''),
      startBufferOffset: row.atoms[0]?.sourceStart ?? row.sourceStart,
    })),
    cursorRow: result.cursorRow,
    cursorCol: result.cursorColumn,
  };
}

/** `null` is retained for the chat history layer's visual-edge fallback contract. */
export function visualUp(state: BufferState, width: number): BufferState | null {
  const result = transition(state, { type: 'moveVisualUp' }, width);
  return result.boundary === 'up' ? null : buffer(result.state, hasDesired(state));
}

export function visualDown(state: BufferState, width: number): BufferState | null {
  const result = transition(state, { type: 'moveVisualDown' }, width);
  return result.boundary === 'down' ? null : buffer(result.state, hasDesired(state));
}
