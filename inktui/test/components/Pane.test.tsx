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
import { PANE_BORDER_GLYPHS, SCROLL_THUMB, TRI_DOWN, TRI_UP } from '../../src/components/glyphs.js';
import {
  COMPACT_PANE_PADDING_CW,
  Pane,
  paneChrome,
  paneColors,
  paneContentWidthForWidth,
  paneHorizontalPaddingForWidth,
} from '../../src/components/Pane.js';
import { buildTheme } from '../../src/theme/buildTheme.js';
import { DEFAULT_THEME_ID, getPalette, getThemeMeta } from '../../src/theme/palettes.js';

const defaultPalette = getPalette(DEFAULT_THEME_ID);
if (defaultPalette === undefined) {
  throw new Error('missing default palette');
}
const theme = buildTheme(defaultPalette, getThemeMeta(DEFAULT_THEME_ID)?.variant ?? 'dark');
import { inkTestColorOn } from '../inkTestColorOn.js';

const B = PANE_BORDER_GLYPHS.bold;
const R = PANE_BORDER_GLYPHS.round;

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

/** ink-testing-library omits ANSI unless chalk level >= 3 (FORCE_COLOR=3); color-gated cases run only then. */
const colorOn = inkTestColorOn();

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
    expect(lines[0]).toContain(`${B.topLeftPrefix}Plans`);
    expect(lines[0]).toContain(B.topRight);
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
    expect(frame).toContain(B.vertical);
    expect(frame).toContain(B.bottomLeft);
    expect(frame).toContain(B.bottomRight);
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

describe('Pane -- focus border weight', () => {
  it('uses bold (heavy) glyphs + Ink borderStyle when focused/highlighted', () => {
    expect(paneChrome(true, theme)).toMatchObject({
      inkBorderStyle: 'bold',
      glyphs: PANE_BORDER_GLYPHS.bold,
    });
  });

  it('uses round (light) glyphs when blurred', () => {
    expect(paneChrome(false, theme)).toMatchObject({
      inkBorderStyle: 'round',
      glyphs: PANE_BORDER_GLYPHS.round,
    });
  });

  it('renders heavy top/side/bottom glyphs when focused', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Plans" focused>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    expect(lines[0]).toContain(`${B.topLeftPrefix}Plans`);
    expect(lines[0]).toContain(B.topRight);
    expect(lines[1]).toContain(B.vertical);
    expect(lines.at(-1)).toContain(B.bottomLeft);
    expect(lines.at(-1)).toContain(B.bottomRight);
  });

  it('renders round glyphs when blurred', () => {
    const { lastFrame } = render(
      <Fixed>
        <Pane title="Plans" focused={false}>
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    expect(lines[0]).toContain(`${R.topLeftPrefix}Plans`);
    expect(lines[0]).toContain(R.topRight);
    expect(lines[1]).toContain(R.vertical);
    expect(lines.at(-1)).toContain(R.bottomLeft);
    expect(lines.at(-1)).toContain(R.bottomRight);
  });
});

describe('Pane -- compact horizontal padding', () => {
  it('drops both horizontal padding columns at the shared cutoff', () => {
    const cutoffWidth = COMPACT_PANE_PADDING_CW + 4;
    expect(paneHorizontalPaddingForWidth(cutoffWidth)).toEqual({
      paddingLeft: 0,
      paddingRight: 0,
    });
    expect(paneContentWidthForWidth(cutoffWidth)).toBe(cutoffWidth - 2);
  });

  it('keeps default horizontal padding above the shared cutoff', () => {
    const roomyWidth = COMPACT_PANE_PADDING_CW + 5;
    expect(paneHorizontalPaddingForWidth(roomyWidth)).toEqual({
      paddingLeft: 1,
      paddingRight: 1,
    });
    expect(paneContentWidthForWidth(roomyWidth)).toBe(roomyWidth - 4);
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

describe('Pane — footer width invariant', () => {
  // The footer (bottom-border overlay) must stay WITHIN the pane's column width: its closing `╯`
  // corner sits on the SAME line as the rest of the footer, never wrapped to its own line, and the
  // footer adds ZERO height (it's a `marginTop:-1` overlay on Ink's own bottom border). The off-by-one
  // bug landed `╯` past the content's right edge, where the real terminal wraps it to a new line.
  // (ink-testing-library clips instead of wrapping — see project_inktui_measure_wrap memory — so this
  // guards the STRUCTURAL invariants the live fix preserves; the visual corner is eyeballed in tmux.)
  it('adds no height: a footer pane has the same line count as the no-footer baseline', () => {
    const baseline = render(
      <Fixed width={32}>
        <Pane title="Chat" focused>
          <Text>{'a\nb\nc'}</Text>
        </Pane>
      </Fixed>,
    );
    const withFooter = render(
      <Fixed width={32}>
        <Pane title="Chat" focused footerLeft={<Text>Cursor</Text>} footerRight={<Text>main</Text>}>
          <Text>{'a\nb\nc'}</Text>
        </Pane>
      </Fixed>,
    );
    const baseLines = (baseline.lastFrame() ?? '').split('\n').length;
    expect((withFooter.lastFrame() ?? '').split('\n')).toHaveLength(baseLines);
  });

  it('keeps the closing corner on the footer line (no bare wrapped ╯ row)', () => {
    const { lastFrame } = render(
      <Fixed width={32}>
        <Pane
          title="Chat"
          focused
          footerLeft={<Text>Cursor ◇ composer-2.5</Text>}
          footerRight={<Text>main</Text>}
        >
          <Text>body</Text>
        </Pane>
      </Fixed>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    // The bottom-left corner identifies the footer line; its closing corner must be on THAT SAME line.
    const footerIdx = lines.findIndex((l) => l.includes(B.bottomLeft));
    expect(footerIdx).toBeGreaterThanOrEqual(0);
    expect(lines[footerIdx]).toContain(B.bottomRight);
    // No line is a lone closing corner (the wrapped-`╯` symptom).
    expect(lines.some((l) => l.trim() === B.bottomRight)).toBe(false);
    // The footer fits within the pane's column width (no line wider than the fixed 32-col frame).
    for (const l of lines) expect(l.length).toBeLessThanOrEqual(32);
  });

  it('scrollbar-variant footer also keeps its corner on the footer line', () => {
    const { lastFrame } = render(
      <Box width={28} height={6}>
        <Pane
          title="Chat"
          focused
          scrollbar={{ height: 4, thumb: null }}
          footerLeft={<Text>Cursor ◇ composer-2.5</Text>}
          footerRight={<Text>main</Text>}
        >
          <Text>body</Text>
        </Pane>
      </Box>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    const footerIdx = lines.findIndex((l) => l.includes(B.bottomLeft));
    expect(footerIdx).toBeGreaterThanOrEqual(0);
    expect(lines[footerIdx]).toContain(B.bottomRight);
    expect(lines.some((l) => l.trim() === B.bottomRight)).toBe(false);
    for (const l of lines) expect(l.length).toBeLessThanOrEqual(28);
  });
});

describe('Pane — scrollbar-as-right-border', () => {
  it('draws the thumb as a full █ run ON the right border, with the corner still closing', () => {
    const { lastFrame } = render(
      <Box width={20} height={6}>
        <Pane title="Doc" focused scrollbar={{ height: 4, thumb: { size: 2, offset: 1 } }}>
          <Text>body</Text>
        </Pane>
      </Box>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    // 6 rows: top border, 4 content rows, bottom border.
    expect(lines).toHaveLength(6);
    // The right EDGE of the content rows is the scroll track: │ █ █ │ (thumb size 2 at offset 1) —
    // there is no separate scrollbar column inside the border.
    expect(lines.slice(1, 5).map((l) => l.at(-1))).toEqual([
      B.vertical,
      SCROLL_THUMB,
      SCROLL_THUMB,
      B.vertical,
    ]);
    // The corners still close: bold top-right tops the track, bold bottom-right ends it.
    expect(lines[0]?.at(-1)).toBe(B.topRight);
    expect(lines[5]?.at(-1)).toBe(B.bottomRight);
    expect(lines[5]).toContain(B.bottomLeft);
  });

  it('a null thumb (content fits) draws a plain │ border — no █ anywhere', () => {
    const { lastFrame } = render(
      <Box width={20} height={6}>
        <Pane title="Doc" focused scrollbar={{ height: 4, thumb: null }}>
          <Text>body</Text>
        </Pane>
      </Box>,
    );
    const lines = (lastFrame() ?? '').split('\n').map(stripAnsi);
    expect(lastFrame() ?? '').not.toContain(SCROLL_THUMB);
    expect(lines.slice(1, 5).map((l) => l.at(-1))).toEqual([
      B.vertical,
      B.vertical,
      B.vertical,
      B.vertical,
    ]);
    expect(lines[5]?.at(-1)).toBe(B.bottomRight);
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
    const bottomLines = lines.filter((l) => l.includes(B.bottomLeft) && l.includes(B.bottomRight));
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
    expect(top).toMatch(
      new RegExp(`${B.horizontal}.*${TRI_UP} 4 .*${TRI_DOWN} 7 .*${B.topRight}`, 'u'),
    );
    // The Ink bottom border is a clean heavy bottom with no triangle leaking onto it.
    expect(bottom).toContain(B.bottomLeft);
    expect(bottom).toContain(B.bottomRight);
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
    expect(top).toContain(B.topRight);
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
