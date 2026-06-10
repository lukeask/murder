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
 *  - Put the list body (a `Ledger`, or any node) as `children`. The Pane provides `paddingX={1}` and
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
import { PaneBorderTop } from './paneBorder.js';

/** Focus-driven colors for the border/corners/fill (`border`) and the title segment (`title`). */
export interface PaneColors {
  readonly border: string;
  readonly title: string;
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
}

/**
 * The bordered Pane. `ref` is forwarded to the OUTER Box for the panel's `useMeasureFocus` (see the
 * header's handoff note). `memo`'d so a Pane repaints only when its own props change (rule 1).
 */
export const Pane = memo(
  forwardRef<DOMElement, PaneProps>(function Pane(
    { title, focused, children, titleExtra, flexGrow = 1 },
    ref,
  ): React.JSX.Element {
    const theme = useTheme();
    const { border: borderColor, title: titleColor } = paneColors(focused, theme);
    return (
      <Box ref={ref} flexDirection="column" flexGrow={flexGrow} minHeight={0} overflow="hidden">
        {/* Top border line with the inline title — the shared {@link ./paneBorder.js} recipe (also
            used by ChatInput). Fixed segments never shrink; the `─` fill absorbs the slack + clips. */}
        <PaneBorderTop
          title={title}
          borderColor={borderColor}
          titleColor={titleColor}
          bold={focused}
          titleExtra={titleExtra}
        />
        {/* Content box supplies the other three sides + padding + height clamp. */}
        <Box
          flexDirection="column"
          flexGrow={1}
          minHeight={0}
          overflow="hidden"
          borderStyle="round"
          borderTop={false}
          borderColor={borderColor}
          paddingX={1}
        >
          {children}
        </Box>
      </Box>
    );
  }),
);
