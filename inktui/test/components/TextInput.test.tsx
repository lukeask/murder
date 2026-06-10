/**
 * TextInput / MultiLineText cursor-rendering tests.
 *
 * Guards the inline-cursor fix: when a value spans multiple rows — either via an explicit `\n`
 * (MultiLineText / new-plan body, note draft) or a soft-wrap across a narrow box (chat input) — the
 * `█` cursor must land immediately after the LAST typed character, NOT at the top-right of the
 * multi-row block. The cursor is rendered as a nested `<Text>` inside the value `<Text>` so it stays
 * in the same text flow; a sibling `<Text>` in the default flex-row `<Box>` regresses to top-right.
 *
 * ink-testing-library's `lastFrame()` gives the rendered text grid, so we assert the cursor sits on
 * the final line right after the last glyph.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import { MultiLineText, TextInput } from '../../src/components/TextInput.js';

const CURSOR = '█';

/** The lines of a rendered frame with trailing blank lines stripped. */
function frameLines(frame: string | undefined): string[] {
  return (frame ?? '').replace(/\n+$/, '').split('\n');
}

describe('TextInput / MultiLineText cursor rendering', () => {
  it('places the cursor after the last char of a multi-line value (explicit newlines)', () => {
    const { lastFrame } = render(
      <MultiLineText value={'first line\nlast line'} focused color="white" />,
    );
    const lines = frameLines(lastFrame());
    // The cursor must be on the final rendered line, immediately after the final character.
    const last = lines.at(-1) ?? '';
    expect(last).toBe(`last line${CURSOR}`);
    // It must NOT be hanging off the end of the first line (the old sibling-flex bug).
    expect(lines[0]).toBe('first line');
    expect(lines[0]).not.toContain(CURSOR);
  });

  it('places the cursor after the last char when a single-line value soft-wraps', () => {
    // Force a wrap by constraining the box width (as ChatInput's bordered box does): the cursor
    // should follow the text onto the final wrapped row, not hang off the first row.
    const text = 'aaaaaaaa bbbbbbbb cccccccc';
    const { lastFrame } = render(
      <Box width={12}>
        <TextInput value={text} focused color="white" />
      </Box>,
    );
    const lines = frameLines(lastFrame());
    expect(lines.length).toBeGreaterThan(1);
    // Last rendered glyph overall is the cursor, and it sits on the final wrapped row.
    expect((lines.at(-1) ?? '').endsWith(CURSOR)).toBe(true);
    // Only one cursor in the whole frame, and it is the very last printable glyph.
    const joined = lines.join('\n');
    expect(joined.match(new RegExp(CURSOR, 'g'))?.length).toBe(1);
    expect(joined.trimEnd().endsWith(CURSOR)).toBe(true);
  });

  it('renders no cursor when not focused', () => {
    const { lastFrame } = render(
      <MultiLineText value={'a\nb'} focused={false} color="white" />,
    );
    expect(lastFrame() ?? '').not.toContain(CURSOR);
  });

  it('keeps the empty-placeholder cursor on the first glyph (unchanged behavior)', () => {
    const { lastFrame } = render(
      <TextInput value="" placeholder="type a message" focused />,
    );
    // No trailing block cursor; the phantom placeholder text is shown.
    const frame = lastFrame() ?? '';
    expect(frame).not.toContain(CURSOR);
    expect(frame).toContain('ype a message');
  });
});
