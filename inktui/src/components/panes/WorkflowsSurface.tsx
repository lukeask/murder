/**
 * WorkflowsSurface — store-free, dimension-driven run-first workflows list
 * (2-row × up-to-5-column layout).
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only.
 */

import type { InkMouseEvent } from '@ink-tools/ink-mouse';
import { Box, Text } from 'ink';
import { memo } from 'react';
import { claimMouseClick } from '@murder/ui-core/input/mouseClick.js';
import type { Theme } from '@murder/ui-core/theme/buildTheme.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import { formatDocTreeName } from './docTreeIndent.js';
import { CreateRow } from './shared/CreateRow.js';
import { dataIndexFromCursor, isCreateCursor } from './shared/createListRow.js';

const PANEL_TITLE = 'Workflows';

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

export type WorkflowsStatusTone = 'error' | 'success' | 'warning' | 'blocked' | 'neutral';

export type WorkflowsDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export type WorkflowsSurfaceRowKind = 'run' | 'node' | 'legacy-ticket-run';

export interface WorkflowsSurfaceRow {
  readonly id: string;
  readonly kind: WorkflowsSurfaceRowKind;
  readonly idCell: string;
  readonly titleCell: string;
  readonly statusCell: string;
  readonly statusTone: WorkflowsStatusTone;
  readonly lastUpdateCell: string;
  readonly depsCell: string;
  readonly depsSatisfied: boolean;
  readonly scheduleCell: string;
  readonly harnessCell: string;
  readonly modelCell: string;
  readonly planCell: string;
  readonly worktreeCell: string;
}

export interface WorkflowsSurfaceProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly theme: Theme;
  readonly rows: readonly WorkflowsSurfaceRow[];
  readonly cursor?: number;
  /** When set, a 1-line “+ create” row is always shown above the ledger (index 0). */
  readonly createLabel?: string;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
  /** Left-click a list row (absolute index, including create at 0 when createLabel is set). */
  readonly onRowClick?: (index: number) => void;
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
export function layout(width: number, height: number): WorkflowsDisplayMode {
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

function linesPerEntryForMode(mode: WorkflowsDisplayMode): number {
  return mode === 'minimal' || mode === 'tiny' ? 1 : LINES_PER_ENTRY;
}

/** Body lines for a mode at a given row count (header excluded). */
function bodyLinesForMode(mode: WorkflowsDisplayMode, rowCount: number): number {
  return rowCount * linesPerEntryForMode(mode);
}

/**
 * Downgrade to single-line when multi-line rows + header would not fit without windowing
 * every row — prefer showing more rows on one line.
 */
export function heightAwareMode(
  mode: WorkflowsDisplayMode,
  innerH: number,
  rowCount: number,
): WorkflowsDisplayMode {
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

function shouldUseMultiColumnLedger(mode: WorkflowsDisplayMode, innerW: number): boolean {
  return mode === 'full' && innerW >= MULTI_COL_INNER_W;
}

function maxColumnsForMode(mode: WorkflowsDisplayMode): number {
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

function showColumnHeader(mode: WorkflowsDisplayMode): boolean {
  return mode === 'full' || mode === 'compact';
}

interface PriorityRowLayout {
  readonly showUpdated: boolean;
  readonly showDeps: boolean;
  readonly showHarness: boolean;
  readonly showPlan: boolean;
}

function priorityRowLayout(innerW: number, mode: WorkflowsDisplayMode): PriorityRowLayout {
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

function statusToneColor(tone: WorkflowsStatusTone, theme: Theme): string {
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
  row: WorkflowsSurfaceRow,
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

function renderPriorityWorkflowEntry(
  row: WorkflowsSurfaceRow,
  ctx: LedgerEntryContext,
  innerW: number,
  mode: WorkflowsDisplayMode,
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
          <Text color={theme.text}>{marker}</Text>
          <Text color={theme.text} bold={ctx.selected}>{row.idCell}</Text>
          {extras.showDeps ? (
            <Text
              color={row.depsSatisfied ? theme.success : theme.warning}
            >{` ${row.depsCell}`}</Text>
          ) : null}
          {extras.showHarness ? <Text color={theme.text}>{` ${row.harnessCell}`}</Text> : null}
          {extras.showPlan ? <Text color={theme.muted}>{` ${row.planCell}`}</Text> : null}
        </Box>
        <Box flexShrink={0}>
          <Text color={statusColor}>{row.statusCell}</Text>
        </Box>
      </Box>
      <Box flexDirection="row" width={innerW} justifyContent="space-between" flexShrink={0}>
        <Box marginLeft={TITLE_INDENT} flexShrink={1} minWidth={0}>
          <Text color={ctx.selected ? theme.text : theme.muted} wrap="truncate">
            {titleText}
          </Text>
        </Box>
        {rowLayout.showUpdated ? (
          <Box flexShrink={0}>
            <Text color={ctx.selected ? theme.text : theme.muted}>{row.lastUpdateCell}</Text>
          </Box>
        ) : null}
      </Box>
    </Box>
  );
}

function renderWorkflowEntry(
  row: WorkflowsSurfaceRow,
  ctx: LedgerEntryContext,
  theme: Theme,
  innerW: number,
): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  const cols = ctx.columns;
  return (
    <Box flexDirection="row" flexGrow={1} flexShrink={0}>
      <Text color={theme.text}>{marker} </Text>
      <Box flexDirection="column" marginRight={2}>
        <Text color={theme.text} bold={ctx.selected}>{row.idCell}</Text>
        <Text color={ctx.selected ? theme.text : theme.muted} wrap="truncate">
          {formatDocTreeName(row.titleCell, innerW)}
        </Text>
      </Box>
      {cols >= 2 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={statusToneColor(row.statusTone, theme)}>{row.statusCell}</Text>
          <Text color={ctx.selected ? theme.text : theme.muted}>{row.lastUpdateCell}</Text>
        </Box>
      ) : null}
      {cols >= 3 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={row.depsSatisfied ? theme.success : theme.warning}>{row.depsCell}</Text>
          <Text color={ctx.selected ? theme.text : theme.muted}>{row.scheduleCell}</Text>
        </Box>
      ) : null}
      {cols >= 4 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={theme.text} bold={ctx.selected}>{row.harnessCell}</Text>
          <Text color={ctx.selected ? theme.text : theme.muted}>{row.modelCell}</Text>
        </Box>
      ) : null}
      {cols >= 5 ? (
        <Box flexDirection="column">
          <Text color={ctx.selected ? theme.text : theme.muted}>{row.planCell}</Text>
          <Text color={ctx.selected ? theme.text : theme.muted}>{row.worktreeCell}</Text>
        </Box>
      ) : null}
    </Box>
  );
}

function renderWorkflowsHeader(columns: number, mode: WorkflowsDisplayMode, theme: Theme): React.ReactNode {
  if (!showColumnHeader(mode)) {
    return null;
  }
  const compact = mode === 'compact' || mode === 'minimal';
  return (
    <Box flexDirection="row" flexShrink={0}>
      <Text color={theme.muted}>{'  '}</Text>
      <Box marginRight={2}>
        <Text color={theme.muted}>{compact ? 'id/title' : 'id / title'}</Text>
      </Box>
      {columns >= 2 ? (
        <Box marginRight={2}>
          <Text color={theme.muted}>{compact ? 'status' : 'status / updated'}</Text>
        </Box>
      ) : null}
      {columns >= 3 ? (
        <Box marginRight={2}>
          <Text color={theme.muted}>{compact ? 'deps' : 'deps / schedule'}</Text>
        </Box>
      ) : null}
      {columns >= 4 ? (
        <Box marginRight={2}>
          <Text color={theme.muted}>{compact ? 'harness' : 'harness / model'}</Text>
        </Box>
      ) : null}
      {columns >= 5 ? (
        <Box>
          <Text color={theme.muted}>{compact ? 'plan' : 'plan / worktree'}</Text>
        </Box>
      ) : null}
    </Box>
  );
}

function renderPriorityHeader(mode: WorkflowsDisplayMode, innerW: number, theme: Theme): React.ReactNode {
  if (!showColumnHeader(mode) || innerW < 18) {
    return null;
  }
  const rowLayout = priorityRowLayout(innerW, mode);
  const compact = mode === 'compact' || mode === 'minimal';
  return (
    <Box flexDirection="row" width={innerW} justifyContent="space-between" flexShrink={0}>
      <Text color={theme.muted}>{compact ? '  id/title' : '  id / title'}</Text>
      <Text color={theme.muted}>{rowLayout.showUpdated ? 'status / updated' : 'status'}</Text>
    </Box>
  );
}

function renderTinyEntry(
  row: WorkflowsSurfaceRow,
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
      <Text color={theme.text} wrap="truncate">
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

function WorkflowsList({
  rows,
  cursor,
  focused,
  width,
  height,
  displayMode,
  status,
  error,
  theme,
  createLabel,
  onRowClick,
}: {
  readonly rows: readonly WorkflowsSurfaceRow[];
  readonly cursor: number;
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
  readonly displayMode: WorkflowsDisplayMode;
  readonly status: 'ready' | 'loading' | 'error';
  readonly error: string | null;
  readonly theme: Theme;
  readonly createLabel?: string;
  readonly onRowClick?: (index: number) => void;
}): React.JSX.Element {
  const innerW = innerWidth(width);
  const innerH = innerHeight(height);
  const showCreate = createLabel !== undefined;
  const createSelected = showCreate && focused && isCreateCursor(cursor);
  const ledgerCursor = showCreate ? (dataIndexFromCursor(cursor) ?? -1) : cursor;
  const ledgerHeight = showCreate ? Math.max(1, innerH - 1) : innerH;
  const multiCol = shouldUseMultiColumnLedger(displayMode, innerW);
  const maxColumns = maxColumnsForMode(displayMode);
  const rowClickProps =
    onRowClick !== undefined
      ? {
          onRowClick: (_row: WorkflowsSurfaceRow, index: number, event: InkMouseEvent) => {
            claimMouseClick(event);
            onRowClick(showCreate ? index + 1 : index);
          },
        }
      : {};

  const createRowEl =
    showCreate && createLabel !== undefined ? (
      <CreateRow
        label={createLabel}
        selected={createSelected}
        width={innerW}
        {...(onRowClick !== undefined
          ? {
              onClick: (event) => {
                claimMouseClick(event);
                onRowClick(0);
              },
            }
          : {})}
      />
    ) : null;

  if (status === 'error') {
    return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (status === 'loading' && rows.length === 0 && !showCreate) {
    return (
      <Text color={theme.muted} wrap="truncate">
        loading…
      </Text>
    );
  }
  if (rows.length === 0 && !showCreate) {
    return (
      <Text color={theme.muted} wrap="truncate">
        no workflows
      </Text>
    );
  }

  const ledgerFocused = focused && !createSelected;
  const singleLine = linesPerEntryForMode(displayMode) === 1;

  const ledger =
    rows.length === 0 ? (
      status === 'loading' ? (
        <Text color={theme.muted} wrap="truncate">
          loading…
        </Text>
      ) : null
    ) : singleLine ? (
      <Ledger
        rows={rows}
        cursor={ledgerCursor}
        focused={ledgerFocused}
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableWidth={innerW}
        availableHeight={ledgerHeight}
        rowKey={(row) => row.id}
        renderEntry={(row, ctx) => renderTinyEntry(row, ctx, innerW, theme)}
        {...rowClickProps}
      />
    ) : multiCol ? (
      <Ledger
        rows={rows}
        cursor={ledgerCursor}
        focused={ledgerFocused}
        linesPerEntry={LINES_PER_ENTRY}
        minColumns={1}
        maxColumns={maxColumns}
        availableWidth={innerW}
        availableHeight={ledgerHeight}
        header={(columns) => renderWorkflowsHeader(columns, displayMode, theme)}
        rowKey={(row) => row.id}
        renderEntry={(row, ctx) => renderWorkflowEntry(row, ctx, theme, innerW)}
        {...rowClickProps}
      />
    ) : (
      <Ledger
        rows={rows}
        cursor={ledgerCursor}
        focused={ledgerFocused}
        linesPerEntry={LINES_PER_ENTRY}
        minColumns={1}
        maxColumns={1}
        availableWidth={innerW}
        availableHeight={ledgerHeight}
        header={() => renderPriorityHeader(displayMode, innerW, theme)}
        rowKey={(row) => row.id}
        renderEntry={(row, ctx) =>
          renderPriorityWorkflowEntry(row, ctx, innerW, displayMode, theme)
        }
        {...rowClickProps}
      />
    );

  return (
    <Box flexDirection="column" flexGrow={1} overflow="hidden">
      {createRowEl}
      {ledger}
    </Box>
  );
}

export const WorkflowsSurface = memo(function WorkflowsSurface({
  width,
  height,
  focused,
  theme,
  rows,
  cursor: cursorProp,
  createLabel,
  status = 'ready',
  error = null,
  onRowClick,
}: WorkflowsSurfaceProps): React.JSX.Element {
  const baseMode = layout(width, height);
  const padding = paneHorizontalPaddingForWidth(width);
  const showCreate = createLabel !== undefined;
  const dataCount = rows.length;
  const totalCount = showCreate ? dataCount + 1 : dataCount;
  const cursor = cursorProp ?? 0;
  const clampedCursor = Math.min(cursor, Math.max(totalCount - 1, 0));
  const innerH = innerHeight(height);
  const innerW = innerWidth(width);
  const ledgerHeight = showCreate ? Math.max(1, innerH - 1) : innerH;
  const displayMode = heightAwareMode(baseMode, ledgerHeight, dataCount);
  const linesPerEntry = linesPerEntryForMode(displayMode);
  const ledgerCursor = showCreate ? (dataIndexFromCursor(clampedCursor) ?? 0) : clampedCursor;
  const hasHeader =
    dataCount > 0 &&
    showColumnHeader(displayMode) &&
    (shouldUseMultiColumnLedger(displayMode, innerW) || innerW >= 18);
  const win = computeWindow(dataCount, ledgerCursor, linesPerEntry, ledgerHeight, hasHeader);
  const overflowAbove = dataCount === 0 ? 0 : win.start;
  const overflowBelow = dataCount === 0 ? 0 : dataCount - win.end;

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
        <WorkflowsList
          rows={rows}
          cursor={clampedCursor}
          focused={focused}
          width={width}
          height={height}
          displayMode={displayMode}
          status={status}
          error={error}
          theme={theme}
          {...(createLabel !== undefined ? { createLabel } : {})}
          {...(onRowClick !== undefined ? { onRowClick } : {})}
        />
      </Pane>
    </Box>
  );
});
