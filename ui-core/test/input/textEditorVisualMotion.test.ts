import { describe, expect, it } from 'vitest';
import {
  plainTextProjection,
  plainTextTopology,
  reduceEditor,
} from '@murder/ui-core/input/textEditor/index.js';

const env = { width: 10, topology: plainTextTopology, projection: plainTextProjection };

describe('shared text editor visual motion', () => {
  it('keeps a desired column through a short row', () => {
    const start = { text: 'abcdefgh\nx\nabcdefgh', cursor: 7, desiredVisualColumn: null } as const;
    const down = reduceEditor(start, { type: 'moveVisualDown' }, env);
    expect(down.state.cursor).toBe(10); // end of the short logical row
    expect(down.state.desiredVisualColumn).toBe(7);
    const again = reduceEditor(down.state, { type: 'moveVisualDown' }, env);
    expect(again.state.cursor).toBe(18); // desired column is restored on the long row
  });

  it('reports visual boundaries for chat history policy', () => {
    const result = reduceEditor(
      { text: 'x', cursor: 0, desiredVisualColumn: null },
      { type: 'moveVisualUp' },
      env,
    );
    expect(result.boundary).toBe('up');
    expect(result.changed).toBe(false);
  });
});
