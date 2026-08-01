import { describe, expect, it } from 'vitest';
import { chatProjection } from '@murder/ui-core/input/chat/chatProjection.js';
import { makeSpan } from '@murder/ui-core/input/chat/chatSpans.js';
import { chatTopology } from '@murder/ui-core/input/chat/chatTopology.js';
import { layoutEditor } from '@murder/ui-core/input/textEditor/layout.js';
import { reduceEditor } from '@murder/ui-core/input/textEditor/operations.js';

const env = {
  width: 40,
  topology: chatTopology,
  projection: chatProjection,
};

describe('chat text adapters', () => {
  it('snaps the cursor around marked image spans', () => {
    const span = makeSpan('img-1');
    const text = `hello${span}world`;
    const inside = span.length / 2 + 'hello'.length;
    expect(chatTopology.normalizeCursor(text, inside, 'nearest')).toBe('hello'.length);
    expect(chatTopology.nextBoundary(text, 'hello'.length)).toBe('hello'.length + span.length);
    expect(chatTopology.previousBoundary(text, 'hello'.length + span.length)).toBe('hello'.length);
  });

  it('deletes marked spans atomically with removedAtom metadata', () => {
    const span = makeSpan('img-2');
    const text = `a${span}b`;
    const after = reduceEditor(
      { text, cursor: 1 + span.length, desiredVisualColumn: null },
      { type: 'backspace' },
      env,
    );
    expect(after.state.text).toBe('ab');
    expect(after.state.cursor).toBe(1);
    expect(after.effects).toEqual([{ type: 'textChanged' }, { type: 'removedAtom', id: 'img-2' }]);
  });

  it('projects image spans as atomic [Image N] labels', () => {
    const span = makeSpan('img-3');
    const text = `x${span}y`;
    const atoms = chatProjection(text);
    expect(atoms.some((atom) => atom.text === '[Image 1]' && atom.atomic === true)).toBe(true);
    const layout = layoutEditor({ text, cursor: 1, desiredVisualColumn: null }, 40, chatProjection);
    expect(layout.rows[0]?.atoms.map((atom) => atom.text).join('')).toContain('[Image 1]');
  });
});
