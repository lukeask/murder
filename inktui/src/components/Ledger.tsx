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
 * Even/odd entries alternate a subtle background (`#1e1e2e` / none). Selection overrides alt-bg.
 * Parity is by absolute row index so it stays stable as the window scrolls.
 *
 * ## Overflow windowing (real, not fake clip)
 * The visible slice is computed from `cursor` + `availableHeight / linesPerEntry`, keeping the
 * cursor on screen (see {@link computeWindow}):
 *  - more rows BELOW the window → reserve the bottom line for a `…` indicator.
 *  - scrolled down (rows hidden ABOVE) → the header/titles row is dropped and replaced by a top `…`
 *    indicator (you can't show titles and a "more above" marker at once in a tight budget).
 *  - `flexShrink={0}` stays on each entry row so Yoga doesn't sample/drop lines within the window.
 *
 * ## Sizing — props, not self-measurement (Phase 1 choice)
 * Ledger receives `availableHeight`/`availableWidth` as PROPS rather than measuring itself. This is
 * the spec-sanctioned testable choice: tests feed an exact budget and assert the slice + indicators
 * deterministically. **Phase 2 handoff:** the Pane measures its inner content size (its own
 * `useMeasureFocus`-style rect minus border/padding) and passes it down as these two props. Until
 * then a panel can pass a fixed budget. Ledger never reads the store or the terminal.
 *
 * ## Rules
 *  - Presentational (rule 1): pure function of props, no store/selector/bus, no `useInput` (rule 5).
 *  - j/k movement is the PANEL's keymap; Ledger only reflects `cursor`. The panel owns cursor state.
 */

import { Box, Text } from 'ink';

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
  /** Total vertical lines the Ledger may use (from the Pane's inner height — see header). */
  readonly availableHeight: number;
  /** Total columns the Ledger may use (from the Pane's inner width — see header). */
  readonly availableWidth: number;
  /** Renders one row's cells for the active `ctx.columns`/`linesPerEntry`. */
  readonly renderEntry: (row: Row, ctx: LedgerEntryContext) => React.ReactNode;
  /** Optional column-titles block (`columns × linesPerEntry`); dropped when scrolled. */
  readonly header?: (columns: number) => React.ReactNode;
  /** Stable key for a row (defaults to the row index). */
  readonly rowKey?: (row: Row, index: number) => string;
}

/** Subtle alternating-background shade (matches TicketsPanel's `#1e1e2e`). */
const ALT_BG = '#1e1e2e';
/** Approximate terminal columns one field needs before Ledger drops to fewer columns. */
const WIDTH_PER_COLUMN = 18;

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
  /** Rows exist above the window → show a top `…` (and suppress the header). */
  readonly moreAbove: boolean;
  /** Rows exist below the window → reserve the bottom line for a `…`. */
  readonly moreBelow: boolean;
}

/**
 * Pure windowing kernel. Given the row count, cursor, lines-per-entry, available height, and whether
 * a header is present, compute the visible slice keeping the cursor on screen and decide the `…`
 * indicators. Reserving a line for an indicator costs entry capacity, so this is iterative but
 * bounded: at most a couple of passes (add bottom `…`, then top `…`, re-fit). Exported for tests.
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

  // Cheap case: everything fits with the header shown and no `…` reserved → no scrolling.
  const headerLines = hasHeader ? linesPerEntry : 0;
  const fullCapacity = Math.floor((availableHeight - headerLines) / linesPerEntry);
  if (fullCapacity >= rowCount && fullCapacity >= 1) {
    return { start: 0, end: rowCount, moreAbove: false, moreBelow: false };
  }

  // We are scrolling. The presence of indicators changes how many entry lines remain, which changes
  // the window, which changes the indicators — so iterate to a fixed point over the candidate
  // `{moreAbove, moreBelow}` flags (at most a few passes; bounded). Each pass: spend lines on the
  // indicators we currently believe are present (top `…` replaces the header; bottom `…` costs one
  // line), fit as many entries as the remainder allows, place the window to keep the cursor visible,
  // then recompute the flags from the resulting window.
  let moreAbove = false;
  let moreBelow = false;
  let start = 0;
  let end = rowCount;
  for (let pass = 0; pass < 4; pass++) {
    // Header is shown only when NOT scrolled past the top; a top `…` takes its place (1 line).
    const topLines = moreAbove ? 1 : headerLines;
    const bottomLines = moreBelow ? 1 : 0;
    const capacity = Math.max(
      1,
      Math.floor((availableHeight - topLines - bottomLines) / linesPerEntry),
    );
    // Place the window so the cursor sits within it, preferring the cursor near the bottom edge once
    // we've scrolled (a simple follow-the-cursor window), clamped to the row range.
    start = Math.max(0, Math.min(clampedCursor - capacity + 1, rowCount - capacity));
    end = Math.min(start + capacity, rowCount);
    const nextAbove = start > 0;
    const nextBelow = end < rowCount;
    if (nextAbove === moreAbove && nextBelow === moreBelow) {
      break;
    }
    moreAbove = nextAbove;
    moreBelow = nextBelow;
  }
  return { start, end, moreAbove, moreBelow };
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
  const selected = focused && index === ledgerCursor;
  // Selection background spans the full width; otherwise alternating parity (by absolute index).
  const backgroundColor = selected ? 'blue' : index % 2 === 1 ? ALT_BG : undefined;
  return (
    <Box flexShrink={0} width="100%" backgroundColor={backgroundColor}>
      {renderEntry(row, { selected, focused, columns })}
    </Box>
  );
}

/**
 * The Ledger. Computes the active column count + visible window from its budget props, then paints
 * the optional header (only when not scrolled past the top), the windowed entries with full-width
 * highlight + alternating background, and the `…` overflow indicators. `linesPerEntry` /
 * `min`/`maxColumns` shape the layout; the panel supplies cell placement via `renderEntry`.
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
}: LedgerProps<Row>): React.JSX.Element {
  const columns = columnsForWidth(availableWidth, minColumns, maxColumns);
  const win = computeWindow(
    rows.length,
    cursor,
    linesPerEntry,
    availableHeight,
    header !== undefined,
  );
  // The header shows only when present AND not scrolled past the top (a top `…` replaces it).
  const showHeader = header !== undefined && !win.moreAbove;
  const visible = rows.slice(win.start, win.end);
  return (
    <Box flexDirection="column" flexShrink={0}>
      {win.moreAbove ? <Text dimColor>…</Text> : null}
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
      {win.moreBelow ? <Text dimColor>…</Text> : null}
    </Box>
  );
}
