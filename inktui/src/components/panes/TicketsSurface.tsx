/**
 * TicketsSurface — store-free, dimension-driven tickets list (2-row × up-to-5-column layout).
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only. Matches the old
 * {@link ../TicketsSurface.tsx} multi-column Ledger layout at large sizes.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import type { Theme } from '../../theme/buildTheme.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import { formatDocTreeName } from './docTreeIndent.js';

const PANEL_TITLE = 'Tickets';

const LINES_PER_ENTRY = 2;

/** Title row plus bottom border row reserved outside the ledger budget. */
const CHROME_ROWS = 2;

/** Name rule: required names show ≥6 leading chars when longer than 6. */
const MIN_NAME_PREFIX = 6;

/** Cursor marker column budget (marker + trailing space). */
const MARKER_COLS = 2;
/** Title line indent (aligns under id, past marker). */
const TITLE_INDENT = 1;
/** Minimum gap between truncated id block and status glyph. */
const STATUS_GAP = 1;
/** Status glyph column budget. */
const STATUS_COLS = 1;

export type TicketsStatusTone = 'error' | 'success' | 'warning' | 'blocked' | 'neutral';

export type TicketsDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export interface TicketsSurfaceRow {
  readonly id: string;
  readonly idCell: string;
  readonly titleCell: string;
  readonly statusCell: string;
  readonly statusTone: TicketsStatusTone;
  readonly lastUpdateCell: string;
  readonly depsCell: string;
  readonly depsSatisfied: boolean;
  readonly scheduleCell: string;
  readonly harnessCell: string;
  readonly modelCell: string;
  readonly planCell: string;
  readonly worktreeCell: string;
}

export interface TicketsSurfaceProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly theme: Theme;
  readonly rows: readonly TicketsSurfaceRow[];
  readonly cursor?: number;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

function innerWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function innerHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

/**
 * Deterministic size router — centralizes what the pane shows at each allocation.
 */
export function layout(width: number, height: number): TicketsDisplayMode {
  const w = innerWidth(width);
  const h = innerHeight(height);
  if (h < 4 || w < 14) {
    return 'tiny';
  }
  if (h < 5 || w < 20) {
    return 'minimal';
  }
  if (h < 8 || w < 28) {
    return 'compact';
  }
  return 'full';
}

function linesPerEntryForMode(mode: TicketsDisplayMode): number {
  return mode === 'minimal' || mode === 'tiny' ? 1 : LINES_PER_ENTRY;
}

/** Body lines for a mode at a given row count (header excluded). */
function bodyLinesForMode(mode: TicketsDisplayMode, rowCount: number): number {
  return rowCount * linesPerEntryForMode(mode);
}

/**
 * Downgrade to single-line when multi-line rows + header would not fit without windowing
 * every ticket — prefer showing more tickets on one line.
 */
export function heightAwareMode(
  mode: TicketsDisplayMode,
  innerH: number,
  rowCount: number,
): TicketsDisplayMode {
  if (mode === 'tiny' || rowCount === 0) {
    return mode;
  }
  const headerLines = showColumnHeader(mode) ? LINES_PER_ENTRY : 0;
  if (headerLines + bodyLinesForMode(mode, rowCount) <= innerH) {
    return mode;
  }
  if (bodyLinesForMode('minimal', rowCount) <= innerH) {
    return 'minimal';
  }
  return 'tiny';
}

/** Inner width at which the 5-column Ledger layout is used instead of priority rows. */
const MULTI_COL_INNER_W = 72;

function shouldUseMultiColumnLedger(mode: TicketsDisplayMode, innerW: number): boolean {
  return mode === 'full' && innerW >= MULTI_COL_INNER_W;
}

function maxColumnsForMode(mode: TicketsDisplayMode): number {
  switch (mode) {
    case 'full':
      return 5;
    case 'compact':
      return 4;
    case 'minimal':
      return 2;
    case 'tiny':
      return 1;
    default:
      return mode satisfies never;
  }
}

function showColumnHeader(mode: TicketsDisplayMode): boolean {
  return mode === 'full' || mode === 'compact';
}

interface PriorityRowLayout {
  readonly showUpdated: boolean;
  readonly showDeps: boolean;
  readonly showHarness: boolean;
  readonly showPlan: boolean;
}

function priorityRowLayout(innerW: number, mode: TicketsDisplayMode): PriorityRowLayout {
  const reserved = MARKER_COLS + STATUS_GAP + STATUS_COLS;
  const none = {
    showUpdated: false,
    showDeps: false,
    showHarness: false,
    showPlan: false,
  };
  if (mode === 'tiny' || mode === 'minimal') {
    return none;
  }
  const updatedReserve = 8;
  const showUpdated =
    mode !== 'compact' &&
    innerW >= MARKER_COLS + TITLE_INDENT + MIN_NAME_PREFIX + 1 + updatedReserve;
  const showDeps = innerW >= reserved + 8;
  const showHarness = innerW >= reserved + 16;
  const showPlan = innerW >= reserved + 28;
  return { showUpdated, showDeps, showHarness, showPlan };
}

/** Name display rule — scaled tree indent; longer names keep ≥6 leading chars when clipped. */
export function truncateName(name: string, maxLen: number, innerWidth?: number): string {
  return formatDocTreeName(name, innerWidth ?? maxLen, { maxLen });
}

function truncateId(id: string, maxLen: number): string {
  if (maxLen <= 0) {
    return '';
  }
  if (id.length <= maxLen) {
    return id;
  }
  return id.slice(0, maxLen);
}

function statusToneColor(tone: TicketsStatusTone, theme: Theme): string {
  switch (tone) {
    case 'error':
      return theme.error;
    case 'success':
      return theme.success;
    case 'warning':
      return theme.warning;
    case 'blocked':
      return theme.accent;
    default:
      return theme.heading;
  }
}

function fitLine1Extras(
  row: TicketsSurfaceRow,
  rowLayout: PriorityRowLayout,
  innerW: number,
): { readonly showDeps: boolean; readonly showHarness: boolean; readonly showPlan: boolean } {
  const fixed = MARKER_COLS + row.idCell.length + STATUS_GAP + STATUS_COLS;
  let budget = innerW - fixed;
  const showDeps = rowLayout.showDeps && budget >= 1 + row.depsCell.length;
  if (showDeps) {
    budget -= 1 + row.depsCell.length;
  }
  const showHarness = rowLayout.showHarness && budget >= 1 + row.harnessCell.length;
  if (showHarness) {
    budget -= 1 + row.harnessCell.length;
  }
  const showPlan = rowLayout.showPlan && budget >= 1 + row.planCell.length;
  return { showDeps, showHarness, showPlan };
}

function renderPriorityTicketEntry(
  row: TicketsSurfaceRow,
  ctx: LedgerEntryContext,
  innerW: number,
  mode: TicketsDisplayMode,
  theme: Theme,
): React.ReactNode {
  const marker = ctx.selected ? '▌ ' : '  ';
  const rowLayout = priorityRowLayout(innerW, mode);
  const extras = fitLine1Extras(row, rowLayout, innerW);
  const statusColor = statusToneColor(row.statusTone, theme);

  const updatedReserve = rowLayout.showUpdated ? row.lastUpdateCell.length : 0;
  const titleBudget = Math.max(
    0,
    innerW - TITLE_INDENT - (rowLayout.showUpdated ? updatedReserve + 1 : 0),
  );
  const titleText = truncateName(row.titleCell, titleBudget, innerW);

  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Box flexDirection="row" width={innerW} justifyContent="space-between" flexShrink={0}>
        <Box flexDirection="row" flexShrink={1} minWidth={0}>
          <Text>{marker}</Text>
          <Text bold={ctx.selected}>{row.idCell}</Text>
          {extras.showDeps ? (
            <Text
              color={row.depsSatisfied ? theme.success : theme.warning}
            >{` ${row.depsCell}`}</Text>
          ) : null}
          {extras.showHarness ? <Text>{` ${row.harnessCell}`}</Text> : null}
          {extras.showPlan ? <Text dimColor>{` ${row.planCell}`}</Text> : null}
        </Box>
        <Box flexShrink={0}>
          <Text color={statusColor}>{row.statusCell}</Text>
        </Box>
      </Box>
      <Box flexDirection="row" width={innerW} justifyContent="space-between" flexShrink={0}>
        <Box marginLeft={TITLE_INDENT} flexShrink={1} minWidth={0}>
          <Text dimColor={!ctx.selected} wrap="truncate">
            {titleText}
          </Text>
        </Box>
        {rowLayout.showUpdated ? (
          <Box flexShrink={0}>
            <Text dimColor={!ctx.selected}>{row.lastUpdateCell}</Text>
          </Box>
        ) : null}
      </Box>
    </Box>
  );
}

function renderTicketEntry(
  row: TicketsSurfaceRow,
  ctx: LedgerEntryContext,
  theme: Theme,
  innerW: number,
): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  const cols = ctx.columns;
  return (
    <Box flexDirection="row" flexGrow={1} flexShrink={0}>
      <Text>{marker} </Text>
      <Box flexDirection="column" marginRight={2}>
        <Text bold={ctx.selected}>{row.idCell}</Text>
        <Text dimColor={!ctx.selected} wrap="truncate">
          {formatDocTreeName(row.titleCell, innerW)}
        </Text>
      </Box>
      {cols >= 2 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={statusToneColor(row.statusTone, theme)}>{row.statusCell}</Text>
          <Text dimColor={!ctx.selected}>{row.lastUpdateCell}</Text>
        </Box>
      ) : null}
      {cols >= 3 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={row.depsSatisfied ? theme.success : theme.warning}>{row.depsCell}</Text>
          <Text dimColor={!ctx.selected}>{row.scheduleCell}</Text>
        </Box>
      ) : null}
      {cols >= 4 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text bold={ctx.selected}>{row.harnessCell}</Text>
          <Text dimColor={!ctx.selected}>{row.modelCell}</Text>
        </Box>
      ) : null}
      {cols >= 5 ? (
        <Box flexDirection="column">
          <Text dimColor={!ctx.selected}>{row.planCell}</Text>
          <Text dimColor={!ctx.selected}>{row.worktreeCell}</Text>
        </Box>
      ) : null}
    </Box>
  );
}

function renderTicketsHeader(columns: number, mode: TicketsDisplayMode): React.ReactNode {
  if (!showColumnHeader(mode)) {
    return null;
  }
  const compact = mode === 'compact' || mode === 'minimal';
  return (
    <Box flexDirection="row" flexShrink={0}>
      <Text dimColor>{'  '}</Text>
      <Box marginRight={2}>
        <Text dimColor>{compact ? 'id/title' : 'id / title'}</Text>
      </Box>
      {columns >= 2 ? (
        <Box marginRight={2}>
          <Text dimColor>{compact ? 'status' : 'status / updated'}</Text>
        </Box>
      ) : null}
      {columns >= 3 ? (
        <Box marginRight={2}>
          <Text dimColor>{compact ? 'deps' : 'deps / schedule'}</Text>
        </Box>
      ) : null}
      {columns >= 4 ? (
        <Box marginRight={2}>
          <Text dimColor>{compact ? 'harness' : 'harness / model'}</Text>
        </Box>
      ) : null}
      {columns >= 5 ? (
        <Box>
          <Text dimColor>{compact ? 'plan' : 'plan / worktree'}</Text>
        </Box>
      ) : null}
    </Box>
  );
}

function renderPriorityHeader(mode: TicketsDisplayMode, innerW: number): React.ReactNode {
  if (!showColumnHeader(mode) || innerW < 18) {
    return null;
  }
  const rowLayout = priorityRowLayout(innerW, mode);
  const compact = mode === 'compact' || mode === 'minimal';
  return (
    <Box flexDirection="row" width={innerW} justifyContent="space-between" flexShrink={0}>
      <Text dimColor>{compact ? '  id/title' : '  id / title'}</Text>
      <Text dimColor>{rowLayout.showUpdated ? 'status / updated' : 'status'}</Text>
    </Box>
  );
}

function renderTinyEntry(
  row: TicketsSurfaceRow,
  ctx: LedgerEntryContext,
  innerW: number,
  theme: Theme,
): React.ReactNode {
  const marker = ctx.selected ? '▌ ' : '  ';
  const statusColor = statusToneColor(row.statusTone, theme);
  const rightReserve = STATUS_GAP + STATUS_COLS;
  const idBudget = Math.max(1, innerW - MARKER_COLS - rightReserve);
  const idText = truncateId(row.idCell, idBudget);
  const titleBudget = Math.max(0, innerW - MARKER_COLS - idText.length - 1 - rightReserve);
  const titleText = truncateName(row.titleCell, titleBudget, innerW);

  return (
    <Box flexDirection="row" width={innerW} justifyContent="space-between" flexShrink={0}>
      <Text wrap="truncate">
        {marker}
        <Text bold={ctx.selected}>{idText}</Text>
        {titleText.length > 0 ? ` ${titleText}` : ''}
      </Text>
      <Box flexShrink={0}>
        <Text color={statusColor}>{row.statusCell}</Text>
      </Box>
    </Box>
  );
}

function TicketsList({
  rows,
  cursor,
  focused,
  width,
  height,
  displayMode,
  status,
  error,
  theme,
}: {
  readonly rows: readonly TicketsSurfaceRow[];
  readonly cursor: number;
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
  readonly displayMode: TicketsDisplayMode;
  readonly status: 'ready' | 'loading' | 'error';
  readonly error: string | null;
  readonly theme: Theme;
}): React.JSX.Element {
  const innerW = innerWidth(width);
  const innerH = innerHeight(height);
  const multiCol = shouldUseMultiColumnLedger(displayMode, innerW);
  const maxColumns = maxColumnsForMode(displayMode);

  if (status === 'error') {
    return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (status === 'loading' && rows.length === 0) {
    return (
      <Text dimColor wrap="truncate">
        loading…
      </Text>
    );
  }
  if (rows.length === 0) {
    return (
      <Text dimColor wrap="truncate">
        no tickets
      </Text>
    );
  }
  const singleLine = linesPerEntryForMode(displayMode) === 1;
  if (singleLine) {
    return (
      <Ledger
        rows={rows}
        cursor={cursor}
        focused={focused}
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableWidth={innerW}
        availableHeight={innerH}
        rowKey={(row) => row.id}
        renderEntry={(row, ctx) => renderTinyEntry(row, ctx, innerW, theme)}
      />
    );
  }

  if (multiCol) {
    return (
      <Ledger
        rows={rows}
        cursor={cursor}
        focused={focused}
        linesPerEntry={LINES_PER_ENTRY}
        minColumns={1}
        maxColumns={maxColumns}
        availableWidth={innerW}
        availableHeight={innerH}
        header={(columns) => renderTicketsHeader(columns, displayMode)}
        rowKey={(row) => row.id}
        renderEntry={(row, ctx) => renderTicketEntry(row, ctx, theme, innerW)}
      />
    );
  }

  return (
    <Ledger
      rows={rows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={LINES_PER_ENTRY}
      minColumns={1}
      maxColumns={1}
      availableWidth={innerW}
      availableHeight={innerH}
      header={() => renderPriorityHeader(displayMode, innerW)}
      rowKey={(row) => row.id}
      renderEntry={(row, ctx) => renderPriorityTicketEntry(row, ctx, innerW, displayMode, theme)}
    />
  );
}

export const TicketsSurface = memo(function TicketsSurface({
  width,
  height,
  focused,
  theme,
  rows,
  cursor: cursorProp,
  status = 'ready',
  error = null,
}: TicketsSurfaceProps): React.JSX.Element {
  const baseMode = layout(width, height);
  const padding = paneHorizontalPaddingForWidth(width);
  const rowCount = rows.length;
  const cursor = cursorProp ?? 0;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));
  const innerH = innerHeight(height);
  const innerW = innerWidth(width);
  const displayMode = heightAwareMode(baseMode, innerH, rowCount);
  const linesPerEntry = linesPerEntryForMode(displayMode);
  const hasHeader =
    rowCount > 0 &&
    showColumnHeader(displayMode) &&
    (shouldUseMultiColumnLedger(displayMode, innerW) || innerW >= 18);
  const win = computeWindow(rowCount, clampedCursor, linesPerEntry, innerH, hasHeader);
  const overflowAbove = rowCount === 0 ? 0 : win.start;
  const overflowBelow = rowCount === 0 ? 0 : rowCount - win.end;

  return (
    <Box width={width} height={height} overflow="hidden">
      <Pane
        title={PANEL_TITLE}
        focused={focused}
        flexGrow={1}
        paddingLeft={padding.paddingLeft}
        paddingRight={padding.paddingRight}
        overflowAbove={overflowAbove}
        overflowBelow={overflowBelow}
      >
        <TicketsList
          rows={rows}
          cursor={clampedCursor}
          focused={focused}
          width={width}
          height={height}
          displayMode={displayMode}
          status={status}
          error={error}
          theme={theme}
        />
      </Pane>
    </Box>
  );
});
