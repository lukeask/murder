/**
 * Ledger — the responsive list-of-entries renderer. The heart of the layout refactor.
 *
 * Every panel's list body (plans, notes, reports, tickets, crows, usage) is the same shape: a
 * vertical run of fixed-height entries, an optional column-title header, a cursor highlight, an
 * alternating background, and overflow when there are more entries than fit. The panels each
 * re-implement that today (and fake the overflow with `flexShrink={0}` + clip). Ledger replaces all
 * of it with one generic primitive; the panel keeps only what's panel-specific: its cursor keymap
 * and how one row's cells map to columns (`renderEntry`).
 *
 * Generic over the row view-model type `Row` (already display-ready — rule 2; Ledger does layout +
 * highlight + overflow + alt-bg, NEVER formatting).
 *
 * ## Responsiveness — column collapse
 * A line of an entry holds up to `maxColumns` horizontally-scaling fields. When the measured width
 * can't fit them all, Ledger drops to fewer (down to `minColumns`); a dropped field simply isn't
 * asked for. The active count is passed to `renderEntry`/`header` as `ctx.columns` so the panel
 * renders exactly that many fields. Ledger does NOT know field semantics — it only decides *how
 * many* fit and lets the panel place them. (Width→columns is a coarse heuristic, see
 * {@link columnsForWidth}; tune the per-column budget there.)
 *
 * ## Full-width highlight
 * The selected row's outer Box is `width="100%"` so its background/inverse spans the FULL Ledger
 * width, not just the text (TicketsPanel's alt-bg is content-width — deliberately NOT copied here).
 * Highlight renders ONLY when `focused`; blurred, the cursor index is remembered (the panel keeps it
 * across blur) but drawn un-highlighted, so re-focusing restores the visible selection.
 *
 * ## Alternating background
 * Even/odd entries alternate a subtle background (`theme.rowAltBg` / none). Selection overrides it.
 * Parity is by absolute row index so it stays stable as the window scrolls.
 *
 * ## Overflow windowing (real, not fake clip)
 * The visible slice is computed from `cursor` + the measured height / linesPerEntry, keeping the
 * cursor on screen with a one-row scrolloff margin (see {@link computeWindow}):
 *  - overflow indicators ("more above/below") are NOT drawn in the interior anymore — they live in the
 *    pane BORDER. The Ledger emits the computed window via `onWindow` and the parent feeds the counts
 *    (above = `start`, below = `rows.length - end`) to the border. Indicators thus cost ZERO interior
 *    lines, so the freed capacity goes to entries and the header is always shown.
 *  - `flexShrink={0}` stays on each entry row so Yoga doesn't sample/drop lines within the window.
 *  - **scrolloff = 1:** the window is placed so at least one row stays visible BOTH above and below
 *    the cursor, except at the list edges (cursor on row 0 has no top margin; cursor on the last row
 *    has no bottom margin). So moving onto the last visible row scrolls one row to keep a row below.
 *
 * ## Sizing — self-measurement (the keystone)
 * Ledger measures its OWN available inner size rather than trusting a fixed prop. The misleading
 * fixed budgets the panels used to pass made `computeWindow` think every row fit (so it never
 * windowed) while the Pane's `overflow="hidden"` silently clipped rows below the fold — the cursor
 * could walk off-screen and never scroll. The fix:
 *  - The Ledger's OUTER Box is a fill box: `flexGrow={1}` + `minHeight={0}` + `overflow="hidden"`, so
 *    it sizes to the Pane's inner content area INDEPENDENT of how many rows it renders. (Critically
 *    NOT `flexShrink={0}` — a content-sized box would measure the rows we drew, not the room we have,
 *    and the measurement would oscillate with the row count, defeating the loop guard.)
 *  - A `useLayoutEffect` (no dep array, runs after every layout) reads the box's measured
 *    `{width, height}` via Ink's `measureElement` and stores it in `useState`. The setter is GUARDED:
 *    it writes ONLY when a dimension actually changed, so a stable layout settles in a single extra
 *    render and never loops. Because the fill box's size is row-count-independent, the second measure
 *    equals the first → the guard holds.
 *  - First paint (before any measurement, or a non-TTY test where Yoga reports 0): fall back to the
 *    optional `availableHeight`/`availableWidth` props if given, else a conservative internal default
 *    ({@link DEFAULT_HEIGHT}×{@link DEFAULT_WIDTH}). So the first frame renders a safe slice around the
 *    cursor — never empty, never a crash, never a loop.
 *  - The `availableHeight`/`availableWidth` props are now OPTIONAL fallbacks, kept so the pure
 *    windowing stays deterministically unit-testable (a test feeds exact budgets via these props and
 *    the measurement never fires under ink-testing-library's sizeless render). Ledger still never
 *    reads the store or the terminal directly.
 *
 * ## Rules
 *  - Presentational (rule 1): pure function of props, no store/selector/bus, no `useInput` (rule 5).
 *  - j/k movement is the PANEL's keymap; Ledger only reflects `cursor`. The panel owns cursor state.
 */

import { Box, type DOMElement, measureElement } from 'ink';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTheme } from '../theme/themeStore.js';

/** Context handed to `renderEntry`/`header` so they emit the right number of fields. */
export interface LedgerEntryContext {
  /** True when this row is the cursor row AND the Ledger is focused (drives highlight). */
  readonly selected: boolean;
  /** True when the owning Pane holds focus (panels may dim cells when blurred). */
  readonly focused: boolean;
  /** Active field count after column collapse — render exactly this many fields per line. */
  readonly columns: number;
}

export interface LedgerProps<Row> {
  /** Display-ready row view-models, already formatted by the selector (rule 2). */
  readonly rows: readonly Row[];
  /** Selected index — owned by the panel via `useState` (Ledger only reflects it). */
  readonly cursor: number;
  /** True when the owning Pane is focused; highlight renders only then. */
  readonly focused: boolean;
  /** Vertical terminal lines each entry occupies (1 usage, 2 plans/notes/tickets). */
  readonly linesPerEntry: number;
  /** Minimum fields-per-line the entry can collapse to. */
  readonly minColumns: number;
  /** Maximum fields-per-line when width allows. */
  readonly maxColumns: number;
  /**
   * OPTIONAL fallback vertical line budget, used only until the Ledger measures its own box (first
   * paint) or when measurement yields 0 (non-TTY test). The Ledger normally self-measures — see the
   * header's "Sizing" note. Tests pass this to drive {@link computeWindow} deterministically.
   */
  readonly availableHeight?: number;
  /** OPTIONAL fallback column budget — see {@link availableHeight}. */
  readonly availableWidth?: number;
  /** Renders one row's cells for the active `ctx.columns`/`linesPerEntry`. */
  readonly renderEntry: (row: Row, ctx: LedgerEntryContext) => React.ReactNode;
  /** Optional column-titles block (`columns × linesPerEntry`); always shown (overflow → border). */
  readonly header?: (columns: number) => React.ReactNode;
  /** Stable key for a row (defaults to the row index). */
  readonly rowKey?: (row: Row, index: number) => string;
  /**
   * Optional callback fired (from an effect, not during render) whenever the computed visible window
   * changes, so the parent can feed the overflow counts to the pane border (above = `win.start`,
   * below = `rows.length - win.end`). Fired only when the window value actually changes.
   */
  readonly onWindow?: (win: LedgerWindow) => void;
}

/** Approximate terminal columns one field needs before Ledger drops to fewer columns. */
const WIDTH_PER_COLUMN = 18;
/** Conservative first-paint dims (before measurement / no fallback prop) — a sane 24×80 screen. */
const DEFAULT_HEIGHT = 24;
const DEFAULT_WIDTH = 80;
/** Rows kept visible above AND below the cursor (except at the list edges). User spec: 1. */
const SCROLLOFF = 1;

/**
 * Coarse width→columns heuristic: how many fields fit in `width`, clamped to `[min, max]`. Pure so
 * the collapse boundary is unit-testable. `WIDTH_PER_COLUMN` is the per-field budget — tune there.
 */
export function columnsForWidth(width: number, minColumns: number, maxColumns: number): number {
  const fit = Math.floor(width / WIDTH_PER_COLUMN);
  return Math.max(minColumns, Math.min(maxColumns, fit));
}

/** The computed visible window: which rows show and which `…` indicators are needed. */
export interface LedgerWindow {
  /** First visible row index (inclusive). */
  readonly start: number;
  /** One past the last visible row index (exclusive). */
  readonly end: number;
  /** Rows exist above the window → the pane border draws a "more above" indicator. */
  readonly moreAbove: boolean;
  /** Rows exist below the window → the pane border draws a "more below" indicator. */
  readonly moreBelow: boolean;
}

/**
 * Pure windowing kernel. Given the row count, cursor, lines-per-entry, available height, and whether
 * a header is present, compute the visible slice keeping the cursor on screen and report the
 * `moreAbove`/`moreBelow` overflow flags. Single-pass: the overflow indicators now live in the pane
 * BORDER (fed via `onWindow`) and cost ZERO interior lines, and the header is always shown, so the
 * entry capacity is a fixed `floor((height - header)/linesPerEntry)` — no fixed-point loop needed.
 * Exported for tests.
 */
export function computeWindow(
  rowCount: number,
  cursor: number,
  linesPerEntry: number,
  availableHeight: number,
  hasHeader: boolean,
): LedgerWindow {
  if (rowCount === 0 || linesPerEntry <= 0 || availableHeight <= 0) {
    return { start: 0, end: 0, moreAbove: false, moreBelow: false };
  }
  const clampedCursor = Math.max(0, Math.min(cursor, rowCount - 1));

  // The header (when present) always shows now — a top `…` no longer replaces it — so it always costs
  // `headerLines`. Indicators are drawn in the border, not the interior, so they cost no entry lines.
  // The capacity is therefore fixed (no longer depends on the overflow flags), so we compute it once.
  const headerLines = hasHeader ? linesPerEntry : 0;
  const capacity = Math.max(1, Math.floor((availableHeight - headerLines) / linesPerEntry));

  // Cheap case: everything fits → no scrolling, no indicators.
  if (capacity >= rowCount) {
    return { start: 0, end: rowCount, moreAbove: false, moreBelow: false };
  }

  // Place the window with a scrolloff margin: keep at least SCROLLOFF rows visible both above and
  // below the cursor (except at the list edges). This is a stateless follow-the-cursor window — we
  // scroll the MINIMUM needed, so we seed `start` at the lowest value that satisfies the bottom
  // margin (cursor sits SCROLLOFF rows from the bottom edge), then cap it so the top margin holds
  // (cursor sits SCROLLOFF rows from the top edge), then clamp to the valid row range:
  //  - minStart = cursor - capacity + 1 + SCROLLOFF  (bottom-margin floor; scrolling onto the last
  //    visible row scrolls one row down so a row stays visible below — the user's exact spec).
  //  - maxStart = cursor - SCROLLOFF                 (top-margin ceiling).
  // The list edges fall out of the [0, rowCount-capacity] clamp for free: cursor 0 forces start 0
  // (no top margin possible), cursor last forces start = rowCount-capacity (no bottom margin).
  const minStart = clampedCursor - capacity + 1 + SCROLLOFF;
  const maxStart = clampedCursor - SCROLLOFF;
  // Seed at the bottom-margin floor (scroll the minimum), but never past the top-margin ceiling.
  let start = Math.min(minStart, maxStart);
  start = Math.max(0, Math.min(start, rowCount - capacity));
  // HARD invariant: the cursor MUST be inside the window — cursor visibility wins over the scrolloff
  // margin. When `capacity` is too small to honour SCROLLOFF on both sides (e.g. capacity 1), the seed
  // above can place `start` off the cursor; this final clamp pulls it back into
  // `[cursor - capacity + 1, cursor]` so the highlight is never scrolled off-screen. Roomy viewports
  // are unaffected (the seed already satisfies this).
  start = Math.max(clampedCursor - capacity + 1, Math.min(start, clampedCursor));
  start = Math.max(0, Math.min(start, rowCount - capacity));
  const end = Math.min(start + capacity, rowCount);
  return { start, end, moreAbove: start > 0, moreBelow: end < rowCount };
}

/**
 * One entry row. `flexShrink={0}` keeps Yoga from sampling lines within the window. Selection gives
 * the row `width="100%"` + a full-width background; otherwise alternating parity supplies the subtle
 * shade (selection overrides alt-bg). Highlight only when `focused`.
 */
function LedgerRow<Row>({
  row,
  index,
  ledgerCursor,
  focused,
  columns,
  renderEntry,
}: {
  readonly row: Row;
  readonly index: number;
  readonly ledgerCursor: number;
  readonly focused: boolean;
  readonly columns: number;
  readonly renderEntry: (row: Row, ctx: LedgerEntryContext) => React.ReactNode;
}): React.JSX.Element {
  const theme = useTheme();
  const selected = focused && index === ledgerCursor;
  // Selection background spans the full width; otherwise alternating parity (by absolute index).
  const backgroundColor = selected
    ? theme.rowSelectedBg
    : index % 2 === 1
      ? theme.rowAltBg
      : undefined;
  return (
    <Box flexShrink={0} width="100%" backgroundColor={backgroundColor}>
      {renderEntry(row, { selected, focused, columns })}
    </Box>
  );
}

/**
 * The Ledger. Self-measures its OUTER fill box (see the header's "Sizing" note) to learn the real
 * inner height/width the Pane gives it, then computes the active column count + visible window from
 * THAT (falling back to the optional props / a default before the first measurement). Paints the
 * optional header (always shown) and the windowed entries with full-width highlight + alternating
 * background. Overflow is no longer drawn in the interior — the computed window is emitted via
 * `onWindow` so the parent can render "more above/below" indicators in the pane border.
 * `linesPerEntry` / `min`/`maxColumns` shape the layout; the panel supplies cell placement via
 * `renderEntry`.
 */
export function Ledger<Row>({
  rows,
  cursor,
  focused,
  linesPerEntry,
  minColumns,
  maxColumns,
  availableHeight,
  availableWidth,
  renderEntry,
  header,
  rowKey,
  onWindow,
}: LedgerProps<Row>): React.JSX.Element {
  const boxRef = useRef<DOMElement | null>(null);
  // Measured inner dims; 0 means "not measured yet" (first paint / sizeless non-TTY render).
  const [measured, setMeasured] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });
  // Runs after every layout (no dep array). Reads the fill box's real size and stores it ONLY when a
  // dimension changed — the guard against a render loop. The fill box is row-count-independent
  // (flexGrow, not flexShrink), so the second measure equals the first and this settles in one pass.
  useLayoutEffect(() => {
    if (boxRef.current === null) {
      return;
    }
    const { width, height } = measureElement(boxRef.current);
    if (width !== measured.width || height !== measured.height) {
      setMeasured({ width, height });
    }
  });

  // Drive layout from the measured dims; fall back to the props (tests), then a conservative default,
  // so the first frame renders a safe slice instead of nothing.
  const effectiveHeight =
    measured.height > 0 ? measured.height : (availableHeight ?? DEFAULT_HEIGHT);
  const effectiveWidth = measured.width > 0 ? measured.width : (availableWidth ?? DEFAULT_WIDTH);

  const columns = columnsForWidth(effectiveWidth, minColumns, maxColumns);
  const win = computeWindow(
    rows.length,
    cursor,
    linesPerEntry,
    effectiveHeight,
    header !== undefined,
  );

  // Keep `onWindow` in a ref so the emit effect can stay keyed on the window VALUE, not the callback
  // identity — the parent typically passes an inline closure that changes every render, and keying the
  // effect on it would re-fire (and risk a setState loop) on every render. We read the latest callback
  // from the ref instead.
  const onWindowRef = useRef(onWindow);
  onWindowRef.current = onWindow;
  // Remember the last-emitted window so we only fire when the value actually changes (the effect's dep
  // tuple already gates most re-runs, but this also guards the first paint vs. measurement re-render).
  const lastEmitted = useRef<{ start: number; end: number; len: number } | null>(null);
  // Capture the scalar window fields + row count so the effect closure depends on the VALUES, not the
  // `win` object identity (which is fresh every render) nor the `onWindow` callback identity (read
  // from the ref). This is what lets an inline parent callback NOT re-fire the emit each render.
  const { start: winStart, end: winEnd, moreAbove, moreBelow } = win;
  const len = rows.length;
  useEffect(() => {
    const prev = lastEmitted.current;
    if (prev !== null && prev.start === winStart && prev.end === winEnd && prev.len === len) {
      return;
    }
    lastEmitted.current = { start: winStart, end: winEnd, len };
    onWindowRef.current?.({ start: winStart, end: winEnd, moreAbove, moreBelow });
  }, [winStart, winEnd, len, moreAbove, moreBelow]);

  // The header is always shown now — overflow indicators live in the pane border, not the interior.
  const showHeader = header !== undefined;
  const visible = rows.slice(win.start, win.end);
  return (
    // Fill box: sizes to the Pane's inner content area regardless of row count (flexGrow + clip), so
    // `measureElement` reports the room we HAVE, not the rows we drew (see the header's "Sizing"note).
    <Box ref={boxRef} flexDirection="column" flexGrow={1} minHeight={0} overflow="hidden">
      {showHeader ? <Box flexShrink={0}>{header(columns)}</Box> : null}
      {visible.map((row, i) => {
        const index = win.start + i;
        return (
          <LedgerRow
            key={rowKey?.(row, index) ?? String(index)}
            row={row}
            index={index}
            ledgerCursor={cursor}
            focused={focused}
            columns={columns}
            renderEntry={renderEntry}
          />
        );
      })}
    </Box>
  );
}
