/**
 * Shared TextEditorDisplay tests.
 *
 * Mid-text cursors inverse-style the grapheme under the cursor (same cell budget as
 * `layoutEditor`). End-of-buffer uses a synthetic blank on its own row when needed.
 */

import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import { TextEditorDisplay } from '../../src/components/TextEditorDisplay.js';
import { layoutEditor } from '@murder/ui-core/input/textEditor/layout.js';
import { plainTextProjection } from '@murder/ui-core/input/textEditor/projection.js';

function frameLines(frame: string | undefined): string[] {
  return (frame ?? '').replace(/\n+$/, '').split('\n');
}

describe('TextEditorDisplay', () => {
  it('renders placeholder text for empty editors', () => {
    const unfocused = render(
      <TextEditorDisplay
        state={{ text: '', cursor: 0, desiredVisualColumn: null }}
        width={20}
        placeholder="type here"
        focused={false}
      />,
    );
    expect(unfocused.lastFrame() ?? '').toContain('type here');

    const focused = render(
      <TextEditorDisplay
        state={{ text: '', cursor: 0, desiredVisualColumn: null }}
        width={20}
        placeholder="type here"
        focused
      />,
    );
    expect(focused.lastFrame() ?? '').toContain('type here');
  });

  it('keeps mid-text cursor rows the same width as layoutEditor', () => {
    const state = { text: '0123456789', cursor: 5, desiredVisualColumn: null } as const;
    const layout = layoutEditor(state, 10, plainTextProjection);
    expect(layout.rows).toHaveLength(1);
    expect(layout.rows[0]?.columns).toBe(10);

    const { lastFrame } = render(<TextEditorDisplay state={state} width={10} focused />);
    // Inverse styling must not insert an extra blank cell that would truncate the final glyph.
    expect(frameLines(lastFrame())[0]).toBe('0123456789');
  });

  it('keeps unfocused text fully visible', () => {
    const { lastFrame } = render(
      <TextEditorDisplay
        state={{ text: 'abc', cursor: 1, desiredVisualColumn: null }}
        width={10}
        focused={false}
      />,
    );
    expect(frameLines(lastFrame())).toEqual(['abc']);
  });

  it('renders wrapped rows and hard newlines explicitly', () => {
    const wrapped = render(
      <TextEditorDisplay
        state={{ text: 'aaaaaaaa bbbbbbbb', cursor: 0, desiredVisualColumn: null }}
        width={8}
        focused={false}
      />,
    );
    expect(frameLines(wrapped.lastFrame()).length).toBeGreaterThan(1);

    const hard = render(
      <TextEditorDisplay
        state={{ text: 'first\nsecond', cursor: 0, desiredVisualColumn: null }}
        width={20}
        focused={false}
      />,
    );
    expect(frameLines(hard.lastFrame())).toEqual(['first', 'second']);
  });

  it('places an end-of-buffer cursor on a synthetic trailing row after a full line', () => {
    const state = { text: '0123456789', cursor: 10, desiredVisualColumn: null } as const;
    const layout = layoutEditor(state, 10, plainTextProjection);
    expect(layout.rows).toHaveLength(2);
    expect(layout.cursorRow).toBe(1);

    const { lastFrame } = render(<TextEditorDisplay state={state} width={10} focused />);
    expect(frameLines(lastFrame())[0]).toBe('0123456789');
  });
});
