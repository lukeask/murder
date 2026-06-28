/**
 * Pane — the bordered, **inline-titled**, focusable container that every list box becomes.
 *
 * Replaces the per-panel copy-paste of `<Box borderStyle="round" …><Text bold>Title</Text>…>`. The
 * difference from the old panels is purely the chrome: the title now sits ON the top border line —
 * `╭─ Plans ──────────────╮` — instead of a plain top border with "Plans" on the next text row.
 *
 * ## How the inline title is drawn (no measurement, no second ref)
 *
 * Ink's `<Box>` borders can't carry a title, so the top border is a hand-composed flex row, and the
 * other three sides come from Ink's own border (`borderStyle="round"` with `borderTop={false}`):
 *
 *   row 1 (this component):  ╭─  Title   ───────────  ╮
 *                            └fixed┘└fixed┘ └fill───┘ └fixed┘
 *   rows 2..n (Ink border):  │  <children>          │
 *                            ╰──────────────────────╯
 *
 * The fill is a `─`-run inside a `flexGrow` + `overflow="hidden"` box: flexbox sizes it to exactly
 * the leftover width and the box clips the overrun (so no `…` ellipsis, no width math). Because both
 * the title row and the bordered content box are `width="100%"`, the `╮`/`╰`/`╯` corners line up
 * over the `│` side borders. This needs **no measured width and no setState** — it can't flicker and
 * it doesn't fight the panel's focus ref (the outer Box keeps the single forwarded ref).
 *
 * Discovered Ink quirks the layout relies on (verified via ink-testing-library before building):
 *  - The CORNER segments (`╭─ `, the leading space, `╮`) are `flexShrink={0}` so the corners are
 *    always drawn at any width. The TITLE segment is `flexShrink={1}` + `overflow="hidden"` so a
 *    title/`titleExtra` wider than the rail truncates instead of pushing `╮` off the edge (the L3b
 *    overflow fix). This works ONLY because the fill box is `flexBasis={0}`: with the default `auto`
 *    basis the 256-char fill would make the row perpetually overflow and Yoga would elide the title
 *    even when wide (the old `Pla…` bug — do NOT revert the title to `flexShrink={0}` or the fill to
 *    `auto` basis). See {@link ./paneBorder.tsx}'s L3b note for the full mechanism.
 *  - The fill text uses `wrap="hard"` (NOT `truncate`): `truncate` appends an ellipsis where we want
 *    a clean `─` edge; `wrap="hard"` hard-wraps the run to box width and `overflow="hidden"` clips it
 *    to one crisp line. (`wrap="end"` is identical visually but not in Ink's `wrap` type.)
 *
 * ## Color / focus
 *  - Border + corner + fill segments: `green` when `focused`, `gray` when blurred.
 *  - Title segment: `green` when `focused`, `white` when blurred (matches the old panels' two-color
 *    scheme — a blurred Pane shows a white title on a gray border, not one uniform shade).
 *
 * ## Rules
 *  - Presentational only (rule 1): a pure function of props, NO store/selector/bus access, NO
 *    `useInput` (rule 5). It receives `focused` and forwards `ref` to its outer Box.
 *  - Formatting stays in selectors (rule 2): `title`/`titleExtra` arrive display-ready.
 *
 * ## Phase 2/3 handoff (prop contract / seam)
 *  - The PANEL owns focus identity. It keeps `useFocusRef` / `useEffectiveFocus` / `useMeasureFocus`
 *    (panel-level — those tie a `PanelId` to a measured rect for directional nav), computes
 *    `focused = useEffectiveFocus() === PANEL_ID`, and passes `focused` + `ref` into the Pane. The
 *    Pane attaches `ref` to its OUTER Box so `useMeasureFocus` measures the whole bordered region
 *    (title row + content) — this keeps the directional-focus rects correct under reflow. Do NOT add
 *    a focus hook inside Pane; that would couple a presentational primitive to panel identity.
 *  - Put the list body (a `Ledger`, or any node) as `children`. The Pane provides `paddingLeft={1}`
 *    and `paddingRight={1}` by default (both configurable for edge-to-edge panes) and
 *    the height-clamping flex discipline (`minHeight={0}` + `overflow="hidden"`) so an overflowing
 *    child clips instead of growing the frame past the terminal height.
 *  - `flexGrow` lets a Rail split its height/width evenly across stacked Panes (default 1).
 *  - `titleExtra` is for a trailing label rendered inside the title segment (e.g. crows' `[max]`
 *    mode indicator), placed right after the title text. It is rendered OUTSIDE the title's colored
 *    `<Text>`, so the CALLER owns its color — pass a styled node (e.g. `<Text dimColor>[max]</Text>`)
 *    rather than expecting it to inherit the green/white title color.
 *  - The ChatInput border can reuse this exact recipe for its `╭─ › ─────────╮` look (spec nit);
 *    if it does, extract the title-row JSX into a small shared helper rather than re-deriving it.
 */

import { Box, type DOMElement } from 'ink';
import { forwardRef, memo } from 'react';
import type { Theme } from '../theme/buildTheme.js';
import { useTheme } from '../theme/themeStore.js';
import {
  PANE_BORDER_GLYPHS,
  type PaneBorderGlyphs,
  type PaneInkBorderStyle,
  paneBorderStyle,
} from './glyphs.js';
import { PaneBorderBottom, PaneBorderRight, PaneBorderTop } from './paneBorder.js';

/** Focus-driven colors for the border/corners/fill (`border`) and the title segment (`title`). */
export interface PaneColors {
  readonly border: string;
  readonly title: string;
}

/** Colors + Ink border style + hand-composed border glyphs for a Pane. */
export interface PaneChrome extends PaneColors {
  readonly inkBorderStyle: PaneInkBorderStyle;
  readonly glyphs: PaneBorderGlyphs;
}

/**
 * Pure color choice for a Pane given focus, resolved through the passed {@link Theme}. A focused Pane
 * uses the focus accent for both border and title; a blurred Pane keeps a readable title
 * (`titleBlurred`) on a recessed border (`borderBlurred`) so it doesn't vanish. Theme is a parameter
 * (not a store read) so the helper stays pure and unit-testable against the theme roles.
 */
export function paneColors(focused: boolean, theme: Theme): PaneColors {
  return focused
    ? { border: theme.focus, title: theme.focus }
    : { border: theme.borderBlurred, title: theme.titleBlurred };
}

/**
 * Full pane chrome for focus: accent colors plus a heavier border (`bold` / `┏━`) when highlighted,
 * round (`╭─`) when blurred. Color-blind aid — weight changes with the same boolean as the green flip.
 */
export function paneChrome(focused: boolean, theme: Theme): PaneChrome {
  const inkBorderStyle = paneBorderStyle(focused);
  return {
    ...paneColors(focused, theme),
    inkBorderStyle,
    glyphs: PANE_BORDER_GLYPHS[inkBorderStyle],
  };
}

/** Comfortable content-width cutoff at/below which panes spend no cells on horizontal padding. */
export const COMPACT_PANE_PADDING_CW = 21;

const PANE_BORDER_COLS = 2;
const DEFAULT_PANE_HORIZONTAL_PADDING = 1;

export interface PaneHorizontalPadding {
  readonly paddingLeft: number;
  readonly paddingRight: number;
}

/** Width-driven horizontal padding shared by pane implementations with an explicit allocation. */
export function paneHorizontalPaddingForWidth(width: number): PaneHorizontalPadding {
  const comfortableContentWidth = Math.max(
    1,
    width - PANE_BORDER_COLS - DEFAULT_PANE_HORIZONTAL_PADDING * 2,
  );
  const padding = comfortableContentWidth <= COMPACT_PANE_PADDING_CW ? 0 : 1;
  return { paddingLeft: padding, paddingRight: padding };
}

/** Content columns after side borders and the width-driven horizontal padding are removed. */
export function paneContentWidthForWidth(width: number): number {
  const padding = paneHorizontalPaddingForWidth(width);
  return Math.max(1, width - PANE_BORDER_COLS - padding.paddingLeft - padding.paddingRight);
}

export interface PaneProps {
  /** Display-ready title shown inline on the top border (formatting lives in the selector). */
  readonly title: string;
  /** True when the owning panel holds the effective focus — flips border + title color. */
  readonly focused: boolean;
  /** The list body (typically a {@link Ledger}) or any node, rendered inside the border. */
  readonly children: React.ReactNode;
  /** Optional trailing label inside the title segment (e.g. crows' `[max]` mode label). */
  readonly titleExtra?: React.ReactNode;
  /** Flex weight for a Rail splitting space across stacked/side-by-side Panes (default 1). */
  readonly flexGrow?: number;
  /** Left padding inside the content box (default 1). Set to 0 for edge-to-edge panes such as chat
   * history and document bodies, where the first content glyph should sit immediately after the left
   * border. */
  readonly paddingLeft?: number;
  /** Right padding inside the content box (default 1). Set to 0 for edge-to-edge panes. Scrollable
   * panes use the right border itself as the scrollbar track, so this is content padding only. */
  readonly paddingRight?: number;
  /** Rows hidden ABOVE the viewport. `> 0` draws a `▴ N` indicator on the top border; 0/undefined
   * leaves the top border byte-identical to today. */
  readonly overflowAbove?: number | undefined;
  /** Rows hidden BELOW the viewport. `> 0` draws a `▾ N` indicator on the TOP border (beside any `▴ N`
   * — see {@link ./paneBorder.js PaneBorderTop}; it lives on the top border, not the bottom, so the
   * bottom can be Ink's clip-robust own border). 0/undefined renders nothing extra. */
  readonly overflowBelow?: number | undefined;
  /** Scrollable panes (doc/chat) pass this to make the RIGHT border double as the scroll track: the
   * thumb is a full `█` run rolling along the `│` side (see {@link ./paneBorder.js PaneBorderRight}).
   * `height` is the pane's measured inner row count (the same fill-box measurement that drives the
   * pane's window); `thumb` is {@link ./panes/docWindow.js computeScrollThumb}'s geometry, `null` when the
   * content fits (the column then draws as a plain border). Omitted → Ink draws the right border as
   * before (non-scrolling consumers are untouched). */
  readonly scrollbar?: {
    readonly height: number;
    readonly thumb: { readonly size: number; readonly offset: number } | null;
  };
  /** Optional left-anchored node on the BOTTOM border (the mirror of `title`). Opting into a footer
   *  replaces Ink's own bottom border with a hand-composed {@link ./paneBorder.js PaneBorderBottom}
   *  row — see that component's fractional-height note for the (opt-in) trade-off. The CALLER owns
   *  the node's color. */
  readonly footerLeft?: React.ReactNode;
  /** Optional right-anchored node on the bottom border — sits the same distance from the right edge
   *  as `title` sits from the left (e.g. a chat pane's `Claude Code ◇ Opus 4.8`). The CALLER owns its
   *  color. Either footer prop turns the bottom border into the hand-composed footer row. */
  readonly footerRight?: React.ReactNode;
}

/**
 * The bordered Pane. `ref` is forwarded to the OUTER Box for the panel's `useMeasureFocus` (see the
 * header's handoff note). `memo`'d so a Pane repaints only when its own props change (rule 1).
 */
export const Pane = memo(
  forwardRef<DOMElement, PaneProps>(function Pane(
    {
      title,
      focused,
      children,
      titleExtra,
      flexGrow = 1,
      paddingLeft = 1,
      paddingRight = 1,
      overflowAbove,
      overflowBelow,
      scrollbar,
      footerLeft,
      footerRight,
    },
    ref,
  ): React.JSX.Element {
    const theme = useTheme();
    const {
      border: borderColor,
      title: titleColor,
      inkBorderStyle,
      glyphs,
    } = paneChrome(focused, theme);
    // A footer (either side) trades Ink's own bottom border for the hand-composed PaneBorderBottom
    // row — see its fractional-height note. Without one, the bottom stays Ink's clip-robust border.
    const hasFooter =
      (footerLeft !== undefined && footerLeft !== null && footerLeft !== false) ||
      (footerRight !== undefined && footerRight !== null && footerRight !== false);
    /* Content box supplies the LEFT (+ RIGHT, unless the right border is the scrollbar track) sides +
       the BOTTOM border + padding + height clamp. `minWidth={0}` is load-bearing in the scrollbar
       variant: there the box sits in a flex ROW beside the `flexShrink={0}` PaneBorderRight column, so
       its default `min-width:auto` (= its content's intrinsic min-width) would refuse to shrink on a narrow
       tiled pane and push the 1-cell right border past the cell's right edge, where the cell's
       `overflow:hidden` clipped it — the missing `━┓` corner on the 2nd pane of a multi-column grid.
       `minWidth={0}` lets the content shrink so the border column always keeps its cell (same fix shape
       as TmuxFrameInline's `minWidth={0}`). Only the TOP is `false` (the hand-composed title row
       draws it). The bottom is Ink's OWN border (`╰──╯`), NOT a separate sibling row: a fixed-height
       bottom row clips off when the pane's height rounds to a half-cell (the `[top, flexGrow, bottom]`
       split loses its trailing fixed cell at fractional heights — e.g. two panels splitting an odd
       rail height), whereas Ink's border, drawn on this flexGrow box, stays on-grid at any height.
       The `▾ N` scroll-below count the old bottom row carried now rides the TOP border. */
    const content = (
      <Box
        flexDirection="column"
        flexGrow={1}
        minWidth={0}
        minHeight={0}
        overflow="hidden"
        borderStyle={inkBorderStyle}
        borderTop={false}
        borderRight={scrollbar === undefined}
        borderColor={borderColor}
        paddingLeft={paddingLeft}
        paddingRight={paddingRight}
      >
        {children}
      </Box>
    );
    // The content region (the box, plus the scroll-track column for a scrollable pane).
    const body =
      scrollbar === undefined ? (
        content
      ) : (
        /* Scrollable pane: the right border IS the scroll track. The content box keeps left/bottom
           (its Ink bottom border ends `╰──` since borderRight is off); the hand-composed column
           draws the `│`/`█` track cells plus the closing `╯` corner — same net width as before. */
        <Box flexDirection="row" flexGrow={1} minWidth={0} minHeight={0} overflow="hidden">
          {content}
          <PaneBorderRight
            height={scrollbar.height}
            thumb={scrollbar.thumb}
            color={borderColor}
            glyphs={glyphs}
          />
        </Box>
      );
    return (
      // `minWidth={0}` on the OUTER box is load-bearing alongside the content box's own `minWidth={0}`
      // (the BUG-7 follow-up): a Box's default `min-width:auto` is its widest child's intrinsic width,
      // and the widest child is the FOOTER/top border row (the full `╰─ harness ◇ model ──── branch ─╯`
      // run + its 256-char `─` fill). Without `minWidth={0}` the outer box refuses to shrink below that
      // intrinsic width on a narrow tiled column, while the inner content box (which HAS `minWidth={0}`)
      // shrinks — so the top border + footer resolve WIDER than the content rows and the footer's fixed
      // `─╯` reserve lands past the content's real right edge, where the real-terminal renderer WRAPS the
      // closing `╯` onto its own line (the off-by-one footer bug). `minWidth={0}` lets the whole Pane
      // shrink as ONE unit so all three rows share the column width. (ink-testing-library clips instead of
      // wrapping, so the corner must be eyeballed live — see project_inktui_measure_wrap memory.)
      <Box
        ref={ref}
        flexDirection="column"
        flexGrow={flexGrow}
        minWidth={0}
        minHeight={0}
        overflow="hidden"
      >
        {/* Top border line with the inline title — the shared {@link ./paneBorder.js} recipe (also
            used by ChatInput). Fixed segments never shrink; the `─` fill absorbs the slack + clips. */}
        <PaneBorderTop
          title={title}
          borderColor={borderColor}
          titleColor={titleColor}
          glyphs={glyphs}
          bold={focused}
          titleExtra={titleExtra}
          overflowAbove={overflowAbove}
          overflowBelow={overflowBelow}
        />
        {body}
        {/* Opt-in footer: an OVERLAY pulled up onto Ink's own (clip-robust) bottom border, carrying
            the left/right labels (e.g. a chat pane's `Claude Code ◇ Opus 4.8`). It adds zero height
            (`marginTop:-1` cancels its 1 row), so the pane is exactly as tall — and as fractional-
            height-robust — as the no-footer case. See {@link ./paneBorder.js PaneBorderBottom}. */}
        {hasFooter && (
          <PaneBorderBottom
            borderColor={borderColor}
            glyphs={glyphs}
            leftExtra={footerLeft}
            rightExtra={footerRight}
          />
        )}
      </Box>
    );
  }),
);
