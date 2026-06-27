/**
 * CrowsPanel — store-free, dimension-driven crows list (new pane contract).
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * Grouped sections flatten into one Ledger with interleaved header rows; a local layout router
 * picks the display mode. Matches {@link ../CrowsPanel.tsx} list intent at large sizes.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { HEALTH_EDGE_COLOR, type Health } from '../../selectors/crowHealthSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';

const PANEL_TITLE = 'Crows';

const CHROME_ROWS = 2;
/** Star column (stacked over readiness glyph) + space before the item title. */
const ROW_GUTTER_COLS = 2;
/** Mirror of {@link ROW_GUTTER_COLS} on the trailing edge so rows do not hug the right `┃`. */
const ROW_RIGHT_GUTTER_COLS = ROW_GUTTER_COLS;
/** Minimum leading characters shown for names longer than this. */
const MIN_NAME_PREFIX = 6;

export type CrowsDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export type CrowsPanelStatus = 'idle' | 'loading' | 'error';

export interface CrowsPanelRow {
  readonly id: string;
  readonly group: string;
  readonly name: string;
  /** Harness · model second line (maximized mode). */
  readonly meta: string;
  readonly working: boolean;
  readonly starred: boolean;
  readonly health: Health;
}

export interface CrowsPanelProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly rows: readonly CrowsPanelRow[];
  readonly cursor?: number;
  /** Minimized (1 line) vs maximized (2 lines) per crow row. */
  readonly expanded?: boolean;
  readonly status?: CrowsPanelStatus;
  readonly error?: string | null;
}

type CrowLedgerRow =
  | { readonly kind: 'header'; readonly group: string; readonly label: string }
  | { readonly kind: 'crow'; readonly row: CrowsPanelRow };

function contentWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function contentHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

/**
 * Deterministic size router — width degrades legend → section headers → meta line → name only.
 * All modes keep every crow row in the Ledger (nothing omitted entirely).
 */
export function layout(width: number, height: number): CrowsDisplayMode {
  const innerH = contentHeight(height);
  const innerW = contentWidth(width);
  if (innerH < 4) {
    return 'minimal';
  }
  if (innerW < 10) {
    return 'tiny';
  }
  if (innerH < 6 || innerW < 16) {
    return 'minimal';
  }
  if (innerH < 8 || innerW < 24) {
    return 'compact';
  }
  return 'full';
}

function nameBudget(innerW: number, inlineGutter = false): number {
  const leftGutter = inlineGutter ? 4 : ROW_GUTTER_COLS;
  return Math.max(1, innerW - leftGutter - ROW_RIGHT_GUTTER_COLS);
}

/** Item title: shorter names in full; longer names keep ≥6 leading chars when truncated. */
export function formatItemTitle(name: string, budget: number): string {
  if (name.length <= MIN_NAME_PREFIX) {
    return name;
  }
  if (budget >= name.length) {
    return name;
  }
  if (budget <= MIN_NAME_PREFIX) {
    return name.slice(0, MIN_NAME_PREFIX);
  }
  if (budget <= MIN_NAME_PREFIX + 1) {
    return name.slice(0, MIN_NAME_PREFIX);
  }
  return `${name.slice(0, budget - 1)}…`;
}

/** Meta second line: full harness · model in `full`; model only when width is tighter. */
export function formatMetaLine(meta: string, mode: CrowsDisplayMode): string {
  const sep = ' · ';
  const splitAt = meta.indexOf(sep);
  if (splitAt === -1) {
    return meta;
  }
  const model = meta.slice(splitAt + sep.length);
  if (mode === 'full') {
    return meta;
  }
  return model;
}

function showSectionHeaders(mode: CrowsDisplayMode): boolean {
  return mode === 'full' || mode === 'compact';
}

function showLegend(mode: CrowsDisplayMode): boolean {
  return mode === 'full';
}

function expandedForMode(mode: CrowsDisplayMode, expanded: boolean): boolean {
  if (mode === 'minimal' || mode === 'tiny') {
    return false;
  }
  return expanded;
}

function countSectionHeaders(rows: readonly CrowsPanelRow[]): number {
  let count = 0;
  let group = '';
  for (const row of rows) {
    if (row.group !== group) {
      group = row.group;
      count += 1;
    }
  }
  return count;
}

/** Ledger line budget for crow rows at one or two lines each (excludes in-band headers). */
function estimateCrowBodyLines(
  crowCount: number,
  headerCount: number,
  hasLegend: boolean,
  linesPerCrow: 1 | 2,
): number {
  return (hasLegend ? 1 : 0) + headerCount + crowCount * linesPerCrow;
}

/** Drop meta lines when maximized rows would not fit without windowing. */
function effectiveShowMeta(
  displayMode: CrowsDisplayMode,
  expanded: boolean,
  innerH: number,
  crowCount: number,
  headerCount: number,
  hasLegend: boolean,
): boolean {
  if (!expandedForMode(displayMode, expanded) || crowCount === 0) {
    return false;
  }
  const maxLines = estimateCrowBodyLines(crowCount, headerCount, hasLegend, 2);
  return maxLines <= innerH;
}

function buildFlatRows(
  rows: readonly CrowsPanelRow[],
  includeHeaders: boolean,
): {
  readonly ledgerRows: readonly CrowLedgerRow[];
  readonly crowToFlat: readonly number[];
} {
  const ledgerRows: CrowLedgerRow[] = [];
  const crowToFlat: number[] = [];
  let group = '';
  for (const row of rows) {
    if (includeHeaders && row.group !== group) {
      group = row.group;
      ledgerRows.push({ kind: 'header', group, label: group });
    }
    crowToFlat.push(ledgerRows.length);
    ledgerRows.push({ kind: 'crow', row });
  }
  return { ledgerRows, crowToFlat };
}

function indicatorColor(health: Health): string {
  return HEALTH_EDGE_COLOR[health];
}

function CrowRowShell({ children }: { readonly children: React.ReactNode }): React.JSX.Element {
  return (
    <Box flexDirection="row" flexGrow={1} flexShrink={0} width="100%">
      <Box flexGrow={1} flexShrink={1} minWidth={0}>
        {children}
      </Box>
      <Box width={ROW_RIGHT_GUTTER_COLS} flexShrink={0} />
    </Box>
  );
}

function renderCrowsLegend(): React.ReactNode {
  return (
    <CrowRowShell>
      <Text dimColor>{'  ○ ready  ● working'}</Text>
    </CrowRowShell>
  );
}

function renderCrowEntry(
  ledgerRow: CrowLedgerRow,
  ctx: LedgerEntryContext,
  displayMode: CrowsDisplayMode,
  showMeta: boolean,
  innerW: number,
): React.ReactNode {
  if (ledgerRow.kind === 'header') {
    return (
      <CrowRowShell>
        <Text dimColor bold wrap="truncate">
          {ledgerRow.label}
        </Text>
      </CrowRowShell>
    );
  }
  const { row } = ledgerRow;
  const color = indicatorColor(row.health);
  const title = formatItemTitle(row.name, nameBudget(innerW, !showMeta));
  const star = row.starred ? '★' : ' ';
  const circle = row.working ? '●' : '○';
  if (!showMeta) {
    return (
      <CrowRowShell>
        <Text wrap="truncate">
          {star} <Text color={color}>{circle}</Text>
          {` ${title}`}
        </Text>
      </CrowRowShell>
    );
  }
  return (
    <CrowRowShell>
      <Box flexDirection="row" flexGrow={1} flexShrink={1} minWidth={0}>
        <Box flexDirection="column" width={1} flexShrink={0}>
          <Text>{star}</Text>
          <Text color={color}>{circle}</Text>
        </Box>
        <Box flexDirection="column" flexGrow={1} flexShrink={1} minWidth={0}>
          <Text wrap="truncate">{` ${title}`}</Text>
          <Text dimColor={!ctx.selected} wrap="truncate">
            {` ${formatMetaLine(row.meta, displayMode)}`}
          </Text>
        </Box>
      </Box>
    </CrowRowShell>
  );
}

function CrowsList({
  rows,
  cursor,
  focused,
  width,
  height,
  displayMode,
  expanded,
  status,
  error,
}: {
  readonly rows: readonly CrowsPanelRow[];
  readonly cursor: number;
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
  readonly displayMode: CrowsDisplayMode;
  readonly expanded: boolean;
  readonly status: CrowsPanelStatus;
  readonly error: string | null;
}): React.JSX.Element {
  const theme = useTheme();
  const innerW = contentWidth(width);
  const innerH = contentHeight(height);
  const includeHeaders = showSectionHeaders(displayMode);
  const hasLegend = showLegend(displayMode) && rows.length > 0;
  const headerCount = includeHeaders ? countSectionHeaders(rows) : 0;
  const showMeta = effectiveShowMeta(
    displayMode,
    expanded,
    innerH,
    rows.length,
    headerCount,
    hasLegend,
  );
  const linesPerEntry = showMeta ? 2 : 1;
  const { ledgerRows, crowToFlat } = useMemo(
    () => buildFlatRows(rows, includeHeaders),
    [rows, includeHeaders],
  );
  const ledgerCursor = crowToFlat[Math.min(cursor, Math.max(crowToFlat.length - 1, 0))] ?? 0;

  if (status === 'error') {
    return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (status === 'loading' && rows.length === 0) {
    return <Text dimColor>loading...</Text>;
  }
  if (rows.length === 0) {
    return <Text dimColor>no crows</Text>;
  }

  return (
    <Ledger
      rows={ledgerRows}
      cursor={ledgerCursor}
      focused={focused}
      linesPerEntry={linesPerEntry}
      minColumns={1}
      maxColumns={1}
      availableWidth={innerW}
      availableHeight={innerH}
      {...(hasLegend ? { header: renderCrowsLegend } : {})}
      rowKey={(ledgerRow) =>
        ledgerRow.kind === 'header' ? `h:${ledgerRow.group}` : `c:${ledgerRow.row.id}`
      }
      renderEntry={(ledgerRow, ctx) =>
        renderCrowEntry(ledgerRow, ctx, displayMode, showMeta, innerW)
      }
    />
  );
}

export const CrowsPanel = memo(function CrowsPanel({
  width,
  height,
  focused,
  rows,
  cursor = 0,
  expanded = true,
  status = 'idle',
  error = null,
}: CrowsPanelProps): React.JSX.Element {
  const padding = paneHorizontalPaddingForWidth(width);
  const displayMode = layout(width, height);
  const includeHeaders = showSectionHeaders(displayMode);
  const innerH = contentHeight(height);
  const hasLegend = showLegend(displayMode) && rows.length > 0;
  const headerCount = includeHeaders ? countSectionHeaders(rows) : 0;
  const showMeta = effectiveShowMeta(
    displayMode,
    expanded,
    innerH,
    rows.length,
    headerCount,
    hasLegend,
  );
  const linesPerEntry = showMeta ? 2 : 1;
  const { ledgerRows, crowToFlat } = useMemo(
    () => buildFlatRows(rows, includeHeaders),
    [rows, includeHeaders],
  );
  const rowCount = crowToFlat.length;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));
  const ledgerCursor = crowToFlat[clampedCursor] ?? 0;
  const win = computeWindow(ledgerRows.length, ledgerCursor, linesPerEntry, innerH, hasLegend);
  const overflowAbove = rowCount === 0 ? 0 : win.start;
  const overflowBelow = rowCount === 0 ? 0 : ledgerRows.length - win.end;

  return (
    <Box width={width} height={height} flexDirection="column" overflow="hidden">
      <Pane
        title={PANEL_TITLE}
        focused={focused}
        flexGrow={1}
        paddingLeft={padding.paddingLeft}
        paddingRight={padding.paddingRight}
        overflowAbove={overflowAbove}
        overflowBelow={overflowBelow}
      >
        <CrowsList
          rows={rows}
          cursor={clampedCursor}
          focused={focused}
          width={width}
          height={height}
          displayMode={displayMode}
          expanded={expanded}
          status={status}
          error={error}
        />
      </Pane>
    </Box>
  );
});
