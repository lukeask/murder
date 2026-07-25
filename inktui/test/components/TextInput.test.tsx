/**
 * Compatibility TextInput rendering tests.
 *
 * The stateful editor display now owns explicit rows and inverse cursor styling. Ink's test frame
 * deliberately omits a trailing inverse blank, so these assertions pin the compatibility wrapper's
 * text rows rather than the former literal `█` glyph.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import { TextInput } from '../../src/components/TextInput.js';

const CURSOR = '█';

/** The lines of a rendered frame with trailing blank lines stripped. */
function frameLines(frame: string | undefined): string[] {
  return (frame ?? '').replace(/\n+$/, '').split('\n');
}

describe('TextInput compatibility rendering', () => {
  it('preserves explicit multi-line rows', () => {
    const { lastFrame } = render(
      <TextInput value={'first line\nlast line'} focused color="white" />,
    );
    const lines = frameLines(lastFrame());
    const last = lines.at(-1) ?? '';
    expect(last).toBe('last line');
    expect(lines[0]).toBe('first line');
    expect(lines[0]).not.toContain(CURSOR);
  });

  it('uses the supplied content width for wrapping', () => {
    const text = 'aaaaaaaa bbbbbbbb cccccccc';
    const { lastFrame } = render(
      <Box width={12}>
        <TextInput value={text} width={12} focused color="white" />
      </Box>,
    );
    const lines = frameLines(lastFrame());
    expect(lines.length).toBeGreaterThan(1);
    expect(lines.join(' ')).toContain('cccccccc');
  });

  it('renders no cursor when not focused', () => {
    const { lastFrame } = render(<TextInput value={'a\nb'} focused={false} color="white" />);
    expect(lastFrame() ?? '').not.toContain(CURSOR);
  });

  it('keeps the empty-placeholder cursor on the first glyph (unchanged behavior)', () => {
    const { lastFrame } = render(<TextInput value="" placeholder="type a message" focused />);
    // No trailing block cursor; the phantom placeholder text is shown.
    const frame = lastFrame() ?? '';
    expect(frame).not.toContain(CURSOR);
    expect(frame).toContain('ype a message');
  });
});
