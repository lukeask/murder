/**
 * Pane test — the bordered, inline-titled focusable container.
 *
 * Tests:
 *  - The title renders ON the top border line (`╭─ Title ──…──╮`), not on a separate row below it.
 *  - Border + title color flip with `focused` (green focused / gray border + white title blurred).
 *  - Children render inside the bordered body, with the bottom border below them.
 *
 * Pane is presentational (no store/bus), so these are plain ink-testing-library render assertions —
 * no AppStore/InputStores harness needed (cf. the panel tests, which wire those for the keymap).
 */

import { Box, Text } from 'ink';
import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import { Pane, paneColors } from '../../src/components/Pane.js';

/** Pane is width-driven by its parent; wrap in a fixed-width Box for deterministic frames. */
function Fixed({ children }: { readonly children: React.ReactNode }): React.JSX.Element {
  return <Box width={28}>{children}</Box>;
}

describe('Pane — inline title border', () => {
  it('renders the title on the top border line, not a separate row', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Plans" focused>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n');
    // First line is the top border carrying the title between the corners.
    expect(lines[0]).toContain('╭─ Plans');
    expect(lines[0]).toContain('╮');
    // The title is NOT a standalone row below the border (the old look). The line after the top
    // border holds the body, not a bare "Plans".
    expect(lines[1]).toContain('body');
    expect(lines[1]?.trim()).not.toBe('Plans');
  });

  it('draws the other three sides and the body between them', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Notes" focused>
          <Text>hello</Text>
        </Pane>
      </Fixed>,
    );
    const frame = lastFrame() ?? '';
    expect(frame).toContain('hello');
    // Side + bottom border glyphs present.
    expect(frame).toContain('│');
    expect(frame).toContain('╰');
    expect(frame).toContain('╯');
  });
});

describe('Pane — focus color', () => {
  // ink-testing-library strips ANSI from `lastFrame()` by default, so the color flip is verified on
  // the pure `paneColors` helper (the single source of truth the component reads).
  it('uses green border + green title when focused', () => {
    expect(paneColors(true)).toEqual({ border: 'green', title: 'green' });
  });

  it('uses gray border + white title when blurred (not uniform)', () => {
    const blurred = paneColors(false);
    expect(blurred).toEqual({ border: 'gray', title: 'white' });
    // The two segments differ when blurred — a white title on a gray border.
    expect(blurred.border).not.toBe(blurred.title);
  });
});

describe('Pane — titleExtra', () => {
  it('renders a trailing label in the title segment', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Crows" focused titleExtra={<Text>{' [max]'}</Text>}>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n');
    expect(lines[0]).toContain('Crows');
    expect(lines[0]).toContain('[max]');
  });
});
