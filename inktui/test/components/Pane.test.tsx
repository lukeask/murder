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
import { theme } from '../../src/theme.js';

/** Pane is width-driven by its parent; wrap in a fixed-width Box for deterministic frames. */
function Fixed({
  children,
  width = 28,
}: {
  readonly children: React.ReactNode;
  readonly width?: number;
}): React.JSX.Element {
  return <Box width={width}>{children}</Box>;
}

/** ink-testing-library strips ANSI unless FORCE_COLOR is set; color-gated cases run only then. */
// biome-ignore lint/complexity/useLiteralKeys: tsc's noPropertyAccessFromIndexSignature requires bracket access on process.env.
const colorOn = Boolean(process.env['FORCE_COLOR']);

/** Strip ANSI SGR escapes so structural (character-position) assertions hold under FORCE_COLOR too. */
// biome-ignore lint/suspicious/noControlCharactersInRegex: matching the ESC control char is the point.
const stripAnsi = (s: string): string => s.replace(/\[[0-9;]*m/g, '');

describe('Pane — inline title border', () => {
  it('renders the title on the top border line, not a separate row', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Plans" focused>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
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
  it('uses the focus accent for both border + title when focused', () => {
    expect(paneColors(true, theme)).toEqual({ border: theme.focus, title: theme.focus });
  });

  it('uses a recessed border + readable title when blurred (not uniform)', () => {
    const blurred = paneColors(false, theme);
    expect(blurred).toEqual({ border: theme.borderBlurred, title: theme.titleBlurred });
    // The two segments differ when blurred — a readable title on a recessed border.
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

describe('Pane — scroll-overflow border indicators', () => {
  it('with no overflow props, the bottom border is a single ╰…╯ line with no triangle', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Notes" focused>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n');
    // Exactly one bottom-border line, and it carries both round corners.
    const bottomLines = lines.filter((l) => l.includes('╰') && l.includes('╯'));
    expect(bottomLines).toHaveLength(1);
    const bottom = bottomLines[0] ?? '';
    // Byte-identical to the old round-style bottom: corners + dashes only, no overflow glyphs.
    expect(bottom).not.toContain('▾');
    expect(bottom).not.toContain('▴');
    // And the top border is unchanged too (no triangle leaks in).
    expect(lines[0]).not.toContain('▴');
    expect(lines[0]).not.toContain('▾');
  });

  it('no-overflow frame has the same line count as the no-overflow baseline', () => {
    const baseline = render(
      <Fixed>
        <Pane title="Notes" focused>
          <Text>{'a\nb\nc'}</Text>
        </Pane>
      </Fixed>,
    );
    const baseLines = (baseline.lastFrame() ?? '').split('\n').length;
    const withProps = render(
      <Fixed>
        <Pane title="Notes" focused overflowAbove={0} overflowBelow={0}>
          <Text>{'a\nb\nc'}</Text>
        </Pane>
      </Fixed>,
    );
    expect((withProps.lastFrame() ?? '').split('\n')).toHaveLength(baseLines);
  });

  it('renders BOTH ▴ N and ▾ N on the top line (right of the dash-fill); bottom is plain ╰…╯', () => {
    const { lastFrame } = render(
      <Fixed width={40}>
        <Pane title="Notes" focused overflowAbove={4} overflowBelow={7}>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    const top = lines[0] ?? '';
    const bottom = lines.at(-1) ?? '';
    // Both indicators ride the TOP border now (the bottom is Ink's own border, which can't carry a
    // count and — unlike a hand-composed bottom row — never clips at fractional pane heights). Order:
    // ▴ 4 (above) then ▾ 7 (below), both after the dash-fill and before the corner ╮.
    expect(top).toMatch(/─.*▴ 4 .*▾ 7 .*╮/u);
    // The Ink bottom border is a clean ╰…╯ with no triangle leaking onto it.
    expect(bottom).toContain('╰');
    expect(bottom).toContain('╯');
    expect(bottom).not.toContain('▾');
    expect(bottom).not.toContain('▴');
  });

  it('keeps the ▴ indicator on a narrow rail even when the long title elides', () => {
    const { lastFrame } = render(
      <Fixed width={24}>
        <Pane title="A very long notes title that elides" focused overflowAbove={4}>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const top = (lastFrame() ?? '').split('\n')[0] ?? '';
    // The fixed indicator never shrinks: triangle + count survive though the title truncates.
    expect(top).toContain('▴');
    expect(top).toContain('4');
    expect(top).toContain('╮');
  });

  it.skipIf(!colorOn)('paints the count dim and the triangle in the border color', () => {
    const { lastFrame } = render(
      <Fixed width={40}>
        <Pane title="Notes" focused overflowAbove={4} overflowBelow={7}>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const frame = lastFrame() ?? '';
    // With color on, the frame retains ANSI escapes around the indicators.
    expect(frame).toContain('▴');
    expect(frame).toContain('▾');
    // Dim SGR (code 2) appears in the frame for the count styling (the `[2m` escape body).
    expect(frame).toContain('[2m');
  });
});
