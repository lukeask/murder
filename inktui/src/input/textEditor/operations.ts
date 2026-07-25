import type { EditorCommand } from './commands.js';
import { layoutEditor, sourceOffsetAt } from './layout.js';
import type { DisplayProjection } from './projection.js';
import { normalizeEditorState, type TextEditorState } from './state.js';
import type { TextTopology } from './topology.js';

export interface EditorEnvironment {
  readonly width: number;
  readonly topology: TextTopology;
  readonly projection: DisplayProjection;
}

export type EditorEffect =
  | { readonly type: 'textChanged' }
  | { readonly type: 'removedAtom'; readonly id: string };

export interface EditorTransition {
  readonly state: TextEditorState;
  readonly changed: boolean;
  readonly effects: readonly EditorEffect[];
  /** Up/Down was attempted beyond the visual layout edge. */
  readonly boundary?: 'up' | 'down';
}

function unchanged(state: TextEditorState, boundary?: 'up' | 'down'): EditorTransition {
  return boundary === undefined
    ? { state, changed: false, effects: [] }
    : { state, changed: false, effects: [], boundary };
}

/** Nonvertical commands clear sticky column even when they cannot move or delete. */
function clearDesired(state: TextEditorState): EditorTransition {
  if (state.desiredVisualColumn === null) return unchanged(state);
  return {
    state: { text: state.text, cursor: state.cursor, desiredVisualColumn: null },
    changed: true,
    effects: [],
  };
}

function moved(state: TextEditorState, cursor: number, topology: TextTopology): EditorTransition {
  const next = {
    text: state.text,
    cursor: topology.normalizeCursor(state.text, cursor, 'nearest'),
    desiredVisualColumn: null,
  } satisfies TextEditorState;
  return next.cursor === state.cursor && state.desiredVisualColumn === null
    ? unchanged(state)
    : {
        state: next,
        changed:
          next.cursor !== state.cursor || next.desiredVisualColumn !== state.desiredVisualColumn,
        effects: [],
      };
}

function wordForward(text: string, cursor: number, topology: TextTopology): number {
  let at = cursor;
  while (at < text.length && !/\s/.test(text[at] ?? '')) at = topology.nextBoundary(text, at);
  while (at < text.length && /\s/.test(text[at] ?? '')) at = topology.nextBoundary(text, at);
  return at;
}

function wordBackward(text: string, cursor: number, topology: TextTopology): number {
  let at = cursor;
  while (at > 0 && /\s/.test(text.slice(topology.previousBoundary(text, at), at)))
    at = topology.previousBoundary(text, at);
  while (at > 0 && !/\s/.test(text.slice(topology.previousBoundary(text, at), at)))
    at = topology.previousBoundary(text, at);
  return at;
}

function wordEnd(text: string, cursor: number, topology: TextTopology): number {
  let at = cursor;
  if (at < text.length) at = topology.nextBoundary(text, at);
  while (at < text.length && /\s/.test(text.slice(at, topology.nextBoundary(text, at))))
    at = topology.nextBoundary(text, at);
  while (at < text.length && !/\s/.test(text.slice(at, topology.nextBoundary(text, at)))) {
    const next = topology.nextBoundary(text, at);
    if (next >= text.length || /\s/.test(text.slice(next, topology.nextBoundary(text, next))))
      return at;
    at = next;
  }
  return at;
}

export function reduceEditor(
  supplied: TextEditorState,
  command: EditorCommand,
  environment: EditorEnvironment,
): EditorTransition {
  const state = normalizeEditorState(supplied, environment.topology);
  const { text, cursor, topology } = {
    text: state.text,
    cursor: state.cursor,
    topology: environment.topology,
  };
  switch (command.type) {
    case 'insert':
    case 'insertNewline': {
      const inserted = command.type === 'insertNewline' ? '\n' : command.text;
      if (inserted.length === 0) return unchanged(state);
      const nextText = text.slice(0, cursor) + inserted + text.slice(cursor);
      // Advance past any grapheme the insertion end lands inside (e.g. combining marks).
      const nextCursor = topology.normalizeCursor(nextText, cursor + inserted.length, 'forward');
      return {
        state: { text: nextText, cursor: nextCursor, desiredVisualColumn: null },
        changed: true,
        effects: [{ type: 'textChanged' }],
      };
    }
    case 'backspace': {
      const specialized = topology.deleteBefore?.(state);
      if (specialized !== undefined && specialized !== null) {
        const effects: EditorEffect[] = [{ type: 'textChanged' }];
        if (specialized.removedAtomId !== undefined)
          effects.push({ type: 'removedAtom', id: specialized.removedAtomId });
        return {
          state: { text: specialized.text, cursor: specialized.cursor, desiredVisualColumn: null },
          changed: true,
          effects,
        };
      }
      if (cursor === 0) return clearDesired(state);
      const start = topology.previousBoundary(text, cursor);
      return {
        state: {
          text: text.slice(0, start) + text.slice(cursor),
          cursor: start,
          desiredVisualColumn: null,
        },
        changed: true,
        effects: [{ type: 'textChanged' }],
      };
    }
    case 'deleteForward': {
      const specialized = topology.deleteAt?.(state);
      if (specialized !== undefined && specialized !== null) {
        const effects: EditorEffect[] = [{ type: 'textChanged' }];
        if (specialized.removedAtomId !== undefined)
          effects.push({ type: 'removedAtom', id: specialized.removedAtomId });
        return {
          state: { text: specialized.text, cursor: specialized.cursor, desiredVisualColumn: null },
          changed: true,
          effects,
        };
      }
      if (cursor >= text.length) return clearDesired(state);
      const end = topology.nextBoundary(text, cursor);
      return {
        state: { text: text.slice(0, cursor) + text.slice(end), cursor, desiredVisualColumn: null },
        changed: true,
        effects: [{ type: 'textChanged' }],
      };
    }
    case 'moveLeft':
      return cursor === 0
        ? clearDesired(state)
        : moved(state, topology.previousBoundary(text, cursor), topology);
    case 'moveRight':
      return cursor >= text.length
        ? clearDesired(state)
        : moved(state, topology.nextBoundary(text, cursor), topology);
    case 'moveLineStart':
      return moved(state, text.lastIndexOf('\n', cursor - 1) + 1, topology);
    case 'moveLineEnd': {
      const next = text.indexOf('\n', cursor);
      return moved(state, next === -1 ? text.length : next, topology);
    }
    case 'moveBufferStart':
      return moved(state, 0, topology);
    case 'moveBufferEnd':
      return moved(state, text.length, topology);
    case 'moveWordForward':
      return moved(state, wordForward(text, cursor, topology), topology);
    case 'moveWordBackward':
      return moved(state, wordBackward(text, cursor, topology), topology);
    case 'moveWordEnd':
      return moved(state, wordEnd(text, cursor, topology), topology);
    case 'moveVisualUp':
    case 'moveVisualDown': {
      const layout = layoutEditor(state, environment.width, environment.projection);
      const direction = command.type === 'moveVisualUp' ? -1 : 1;
      const targetRow = layout.cursorRow + direction;
      if (targetRow < 0 || targetRow >= layout.rows.length)
        return unchanged(state, direction < 0 ? 'up' : 'down');
      const desired = state.desiredVisualColumn ?? layout.cursorColumn;
      const target = layout.rows[targetRow];
      if (target === undefined) return unchanged(state, direction < 0 ? 'up' : 'down');
      const targetCursor = topology.normalizeCursor(
        text,
        sourceOffsetAt(target, Math.min(desired, target.columns)),
        'nearest',
      );
      const next: TextEditorState = { text, cursor: targetCursor, desiredVisualColumn: desired };
      return {
        state: next,
        changed:
          next.cursor !== state.cursor || next.desiredVisualColumn !== state.desiredVisualColumn,
        effects: [],
      };
    }
    default:
      return command satisfies never;
  }
}
