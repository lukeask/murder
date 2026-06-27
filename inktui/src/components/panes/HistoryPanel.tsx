/**
 * HistoryPanel — explicit width/height pane contract for the user-intention history feed.
 *
 * Store-free: callers pass display-ready rows and chrome props. Matches the old
 * {@link ../HistoryPanel.tsx} list layout at large sizes; smaller allocations route through
 * {@link layout} into deterministic display modes.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import type { Theme } from '../../theme/buildTheme.js';
import { useTheme } from '../../theme/themeStore.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';

/** How many wrapped lines the intention text gets (entry height = 1 metadata line + this). */
const INTENTION_LINES = 2;

const CHROME_ROWS = 2;
/** `╭─ `, trailing space, and `╮` reserved outside the inline title segment. */
const TITLE_CHROME_COLS = 5;

/** Border tail for one scroll-overflow count: ` ─ ▾ N ──`. */
function overflowIndicatorCols(count: number): number {
  if (count <= 0) {
    return 0;
  }
  return 8 + String(count).length;
}

function overflowChromeCols(overflowAbove: number, overflowBelow: number): number {
  return overflowIndicatorCols(overflowAbove) + overflowIndicatorCols(overflowBelow);
}

/**
 * Pane border title — full digest at wide rails; shorter variants before Pane clips with `…`.
 */
export function formatPaneTitle(
  width: number,
  looseCount: number,
  mode: HistoryPanelMode,
  overflowAbove = 0,
  overflowBelow = 0,
): string {
  const budget = Math.max(
    1,
    width - TITLE_CHROME_COLS - overflowChromeCols(overflowAbove, overflowBelow),
  );
  const candidates = [
    mode === 'all' ? `History · ${looseCount} loose · all` : `History · ${looseCount} loose`,
    mode === 'all' ? `History · ${looseCount} · all` : `History · ${looseCount}`,
    `Hist · ${looseCount}`,
    `H·${looseCount}`,
    'History',
    'Hist',
  ];
  for (const candidate of candidates) {
    if (candidate.length <= budget) {
      return candidate;
    }
  }
  return (candidates[candidates.length - 1] ?? 'History').slice(0, budget);
}

const HEADER_FILLER_KEYS = Array.from(
  { length: INTENTION_LINES - 1 },
  (_, i) => `history-header-filler-${i}`,
);

export type HistoryPanelMode = 'loose' | 'all';

export type HistoryPanelStatus = 'idle' | 'loading' | 'error';

export interface HistoryPanelRow {
  readonly id: string;
  readonly age: string;
  readonly target: string;
  readonly status: string;
  readonly text: string;
}

export type HistoryDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export interface HistoryPanelProps {
  /** Full pane allocation including border, title, footer, and padding. */
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly rows: readonly HistoryPanelRow[];
  /** Loose-thread filter — OPEN+STALE only when `loose` (default). */
  readonly mode?: HistoryPanelMode;
  readonly cursor?: number;
  readonly status?: HistoryPanelStatus;
  readonly error?: string | null;
}

/** Content line budget inside the bordered pane (title row + bottom border excluded). */
function contentHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

/** Text column budget inside pane padding and side borders. */
function contentWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function isLoose(status: string): boolean {
  return status === 'open' || status === 'stale';
}

function looseCount(rows: readonly HistoryPanelRow[]): number {
  return rows.filter((row) => isLoose(row.status)).length;
}

function statusTag(status: string, mode: HistoryDisplayMode): string {
  if (mode === 'tiny' || mode === 'minimal') {
    switch (status) {
      case 'stale':
        return 'S';
      case 'dismissed':
        return 'D';
      default:
        return 'O';
    }
  }
  return status.toUpperCase();
}

function statusColor(status: string, theme: Theme): string {
  switch (status) {
    case 'stale':
      return theme.warning;
    case 'dismissed':
      return theme.muted;
    default:
      return theme.accent;
  }
}

/** Minimum leading characters shown for target names longer than this. */
const MIN_NAME_PREFIX = 6;

/** Target name: shorter names in full; longer names keep ≥6 leading chars when truncated. */
export function formatTargetName(target: string, budget: number): string {
  if (target.length <= MIN_NAME_PREFIX) {
    return target;
  }
  if (budget >= target.length) {
    return target;
  }
  if (budget <= MIN_NAME_PREFIX) {
    return target.slice(0, MIN_NAME_PREFIX);
  }
  if (budget <= MIN_NAME_PREFIX + 1) {
    return target.slice(0, MIN_NAME_PREFIX);
  }
  return `${target.slice(0, budget - 1)}…`;
}

/** Empty-list copy sized to the content column budget — avoids awkward wraps at narrow widths. */
export function emptyStateText(mode: HistoryPanelMode, innerW: number): string {
  const loose = mode === 'loose';
  if (innerW >= 16) {
    return loose ? 'no loose threads' : 'no history';
  }
  if (innerW >= 9) {
    return loose ? 'no loose' : 'no history';
  }
  return loose ? 'none' : 'empty';
}

function ageWidth(mode: HistoryDisplayMode): number {
  switch (mode) {
    case 'full':
      return 8;
    case 'compact':
      return 6;
    case 'minimal':
    case 'tiny':
      return 5;
    default:
      return mode satisfies never;
  }
}

/**
 * Deterministic 2D size router — richest display at the largest allocation; all modes keep every row.
 */
export function layout(width: number, height: number): HistoryDisplayMode {
  const innerH = contentHeight(height);
  const innerW = contentWidth(width);
  if (innerH < 4 || innerW < 12) {
    return 'tiny';
  }
  if (innerW < 20 || innerH < 6) {
    return 'minimal';
  }
  if (innerW < 32 || innerH < 8) {
    return 'compact';
  }
  return 'full';
}

function intentionLinesForMode(mode: HistoryDisplayMode): number {
  switch (mode) {
    case 'full':
    case 'compact':
      return INTENTION_LINES;
    case 'minimal':
      return 1;
    case 'tiny':
      return 0;
    default:
      return mode satisfies never;
  }
}

function linesPerEntryForMode(mode: HistoryDisplayMode): number {
  return 1 + intentionLinesForMode(mode);
}

function showColumnHeader(mode: HistoryDisplayMode): boolean {
  return mode === 'full' || mode === 'compact' || mode === 'minimal';
}

function renderHistoryHeader(displayMode: HistoryDisplayMode): React.ReactNode {
  const linesPerEntry = linesPerEntryForMode(displayMode);
  if (!showColumnHeader(displayMode)) {
    return null;
  }
  if (displayMode === 'minimal') {
    return (
      <Box flexDirection="column" flexShrink={0}>
        <Text dimColor>{' age  target'}</Text>
        {HEADER_FILLER_KEYS.slice(0, Math.max(0, linesPerEntry - 1)).map((key) => (
          <Text key={key}> </Text>
        ))}
      </Box>
    );
  }
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Text dimColor>
        {displayMode === 'compact' ? ' age    target' : '  age      target  status'}
      </Text>
      {intentionLinesForMode(displayMode) > 0 ? <Text dimColor>intention</Text> : null}
      {HEADER_FILLER_KEYS.slice(0, Math.max(0, linesPerEntry - 2)).map((key) => (
        <Text key={key}> </Text>
      ))}
    </Box>
  );
}

function metaPrefixForRow(
  row: HistoryPanelRow,
  marker: string,
  displayMode: HistoryDisplayMode,
  innerW: number,
): { readonly prefix: string; readonly target: string } {
  const tag = statusTag(row.status, displayMode);
  const ageCol = displayMode === 'tiny' ? row.age : row.age.padEnd(ageWidth(displayMode));
  const markerPrefix = displayMode === 'minimal' || displayMode === 'tiny' ? marker : `${marker} `;
  const reserved = markerPrefix.length + ageCol.length + 1 + tag.length + 1;
  const target = formatTargetName(row.target, Math.max(1, innerW - reserved));
  return { prefix: `${markerPrefix}${ageCol} `, target };
}

function renderHistoryEntry(
  row: HistoryPanelRow,
  ctx: LedgerEntryContext,
  displayMode: HistoryDisplayMode,
  innerW: number,
  theme: Theme,
): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  const intentionLines = intentionLinesForMode(displayMode);
  const tag = statusTag(row.status, displayMode);
  const { prefix, target } = metaPrefixForRow(row, marker, displayMode, innerW);

  const metaLine =
    displayMode === 'minimal' || displayMode === 'tiny' ? (
      <Box width={innerW} overflow="hidden">
        <Text wrap="truncate">
          {`${prefix}${target} `}
          <Text color={statusColor(row.status, theme)}>{tag}</Text>
        </Text>
      </Box>
    ) : (
      <Box flexDirection="row" width={innerW} overflow="hidden">
        <Text wrap="truncate">{`${prefix}${target} `}</Text>
        <Text color={statusColor(row.status, theme)}>{tag}</Text>
      </Box>
    );

  if (displayMode === 'tiny') {
    return metaLine;
  }

  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      {metaLine}
      {intentionLines > 0 ? (
        <Box flexDirection="column" flexShrink={0} height={intentionLines} overflow="hidden">
          <Text dimColor={!ctx.selected} wrap="wrap">
            {row.text}
          </Text>
        </Box>
      ) : null}
    </Box>
  );
}

function HistoryList({
  rows,
  mode,
  cursor,
  focused,
  width,
  height,
  displayMode,
  status,
  error,
}: {
  readonly rows: readonly HistoryPanelRow[];
  readonly mode: HistoryPanelMode;
  readonly cursor: number;
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
  readonly displayMode: HistoryDisplayMode;
  readonly status: HistoryPanelStatus;
  readonly error: string | null;
}): React.JSX.Element {
  const theme = useTheme();
  const visibleRows = useMemo(
    () => (mode === 'loose' ? rows.filter((row) => isLoose(row.status)) : rows),
    [mode, rows],
  );
  const linesPerEntry = linesPerEntryForMode(displayMode);
  const innerH = contentHeight(height);
  const innerW = contentWidth(width);
  const hasHeader = showColumnHeader(displayMode);

  if (status === 'error') {
    return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (status === 'loading' && visibleRows.length === 0) {
    return <Text dimColor>loading…</Text>;
  }
  if (visibleRows.length === 0) {
    return (
      <Text dimColor wrap="truncate">
        {emptyStateText(mode, innerW)}
      </Text>
    );
  }

  return (
    <Ledger
      rows={visibleRows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={linesPerEntry}
      minColumns={1}
      maxColumns={1}
      availableWidth={innerW}
      availableHeight={innerH}
      {...(hasHeader ? { header: () => renderHistoryHeader(displayMode) } : {})}
      rowKey={(row) => row.id}
      renderEntry={(row, ctx) => renderHistoryEntry(row, ctx, displayMode, innerW, theme)}
    />
  );
}

export const HistoryPanel = memo(function HistoryPanel({
  width,
  height,
  focused,
  rows,
  mode = 'loose',
  cursor = 0,
  status = 'idle',
  error = null,
}: HistoryPanelProps): React.JSX.Element {
  const padding = paneHorizontalPaddingForWidth(width);
  const displayMode = layout(width, height);
  const visibleRows = mode === 'loose' ? rows.filter((row) => isLoose(row.status)) : rows;
  const clampedCursor = Math.min(cursor, Math.max(visibleRows.length - 1, 0));
  const linesPerEntry = linesPerEntryForMode(displayMode);
  const innerH = contentHeight(height);
  const hasHeader = showColumnHeader(displayMode) && visibleRows.length > 0;
  const win = computeWindow(visibleRows.length, clampedCursor, linesPerEntry, innerH, hasHeader);
  const overflowAbove = visibleRows.length === 0 ? 0 : win.start;
  const overflowBelow = visibleRows.length === 0 ? 0 : visibleRows.length - win.end;
  const title = formatPaneTitle(width, looseCount(rows), mode, overflowAbove, overflowBelow);

  return (
    <Box width={width} height={height} overflow="hidden">
      <Pane
        title={title}
        focused={focused}
        flexGrow={1}
        paddingLeft={padding.paddingLeft}
        paddingRight={padding.paddingRight}
        overflowAbove={overflowAbove}
        overflowBelow={overflowBelow}
      >
        <HistoryList
          rows={rows}
          mode={mode}
          cursor={clampedCursor}
          focused={focused}
          width={width}
          height={height}
          displayMode={displayMode}
          status={status}
          error={error}
        />
      </Pane>
    </Box>
  );
});
