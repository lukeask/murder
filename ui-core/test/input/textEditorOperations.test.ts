import { describe, expect, it } from 'vitest';
import {
  editorAtEnd,
  plainTextProjection,
  plainTextTopology,
  reduceEditor,
} from '@murder/ui-core/input/textEditor/index.js';

const env = { width: 12, topology: plainTextTopology, projection: plainTextProjection };

describe('shared text editor operations', () => {
  it('edits graphemes at an arbitrary cursor position', () => {
    const inserted = reduceEditor(
      { text: 'ac', cursor: 1, desiredVisualColumn: null },
      { type: 'insert', text: 'b' },
      env,
    );
    expect(inserted.state).toEqual({ text: 'abc', cursor: 2, desiredVisualColumn: null });
    const deleted = reduceEditor(editorAtEnd(`a👩‍💻b`), { type: 'backspace' }, env);
    expect(deleted.state.text).toBe('a👩‍💻');
    expect(reduceEditor(deleted.state, { type: 'backspace' }, env).state.text).toBe('a');
  });

  it('normalizes the cursor past combining marks after insert', () => {
    const result = reduceEditor(
      { text: '\u0301', cursor: 0, desiredVisualColumn: 3 },
      { type: 'insert', text: 'a' },
      env,
    );
    expect(result.state.text).toBe('a\u0301');
    expect(result.state.cursor).toBe(2);
    expect(result.state.desiredVisualColumn).toBeNull();
  });

  it('clears desiredVisualColumn on nonvertical no-ops', () => {
    const state = { text: 'ab', cursor: 0, desiredVisualColumn: 5 } as const;
    expect(reduceEditor(state, { type: 'moveLeft' }, env).state.desiredVisualColumn).toBeNull();
    expect(
      reduceEditor({ ...state, cursor: 2 }, { type: 'moveRight' }, env).state.desiredVisualColumn,
    ).toBeNull();
    expect(reduceEditor(state, { type: 'backspace' }, env).state.desiredVisualColumn).toBeNull();
    expect(
      reduceEditor({ ...state, cursor: 2 }, { type: 'deleteForward' }, env).state
        .desiredVisualColumn,
    ).toBeNull();
  });

  it('uses logical-line and buffer motions without geometry', () => {
    const state = { text: 'one\ntwo', cursor: 5, desiredVisualColumn: 7 } as const;
    expect(reduceEditor(state, { type: 'moveLineStart' }, env).state).toEqual({
      text: 'one\ntwo',
      cursor: 4,
      desiredVisualColumn: null,
    });
    expect(reduceEditor(state, { type: 'moveBufferEnd' }, env).state.cursor).toBe(7);
  });
});
