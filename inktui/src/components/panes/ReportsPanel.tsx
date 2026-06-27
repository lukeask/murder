/**
 * ReportsPanel — store-free, dimension-driven reports list for fixtures and the new pane contract.
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only. Doc rows reserve
 * a star column, degrade date/char-count metadata before the item title, and keep ≥6 name prefix
 * chars when truncated.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { useTheme } from '../../theme/themeStore.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import type { ResourceRowFields } from '../ResourceRow.js';
import { formatDocTreeName } from './docTreeIndent.js';

const PANEL_TITLE = 'Reports';

/** Title row plus bottom border row reserved outside the ledger budget. */
const CHROME_ROWS = 2;
/** Reserved star cell plus trailing space before the item title. */
const STAR_GUTTER_COLS = 2;
/** Deterministic presentation modes — richest first. */
export type ReportsDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;

type DateCompress = 'full' | 'monthDay' | 'shortDay' | 'numeric' | 'hidden';

function contentWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function contentHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

/**
 * Deterministic size router — width drives metadata disclosure; height collapses two-line rows
 * to one-line compact/minimal before dropping the column header or entering tiny mode.
 */
export function layout(width: number, height: number): ReportsDisplayMode {
  const innerW = contentWidth(width);
  const innerH = contentHeight(height);
  if (innerH < 4 || innerW < 8) {
    return 'tiny';
  }
  if (innerW < 12) {
    return 'minimal';
  }
  if (innerH < 6 || innerW < 18) {
    return 'minimal';
  }
  if (innerH < 8 || innerW < 26) {
    return 'compact';
  }
  return 'full';
}

function linesPerEntryForMode(mode: ReportsDisplayMode): number {
  switch (mode) {
    case 'full':
    case 'compact':
      return 2;
    case 'minimal':
    case 'tiny':
      return 1;
    default:
      return mode satisfies never;
  }
}

function showColumnHeader(mode: ReportsDisplayMode, innerH: number): boolean {
  if (mode === 'minimal' || mode === 'tiny' || innerH < 6) {
    return false;
  }
  return true;
}

function headerShowsSize(mode: ReportsDisplayMode, innerW: number): boolean {
  if (!showCharCount(mode)) {
    return false;
  }
  const sample = formatMetaLine(
    { name: '', charCount: '12.4k', updatedAt: 'Jun. 21 09:32', starred: false },
    mode,
    innerW,
  );
  return sample.includes('·');
}

function renderReportsHeader(
  mode: ReportsDisplayMode,
  innerW: number,
  innerH: number,
): React.ReactNode {
  if (!showColumnHeader(mode, innerH)) {
    return null;
  }
  if (mode === 'full' && innerH >= 9) {
    const line2 = headerShowsSize(mode, innerW) ? 'size · updated' : 'updated';
    if (line2.length <= innerW) {
      return (
        <Box flexDirection="column" flexShrink={0}>
          <Text dimColor>{'name'}</Text>
          <Text dimColor>{line2}</Text>
        </Box>
      );
    }
  }
  if (innerW >= 4) {
    return (
      <Box flexShrink={0}>
        <Text dimColor>{'name'}</Text>
      </Box>
    );
  }
  return null;
}

function showCharCount(mode: ReportsDisplayMode): boolean {
  return mode === 'full' || mode === 'compact';
}

function dateCompressForMode(mode: ReportsDisplayMode): DateCompress {
  switch (mode) {
    case 'full':
      return 'full';
    case 'compact':
      return 'monthDay';
    case 'minimal':
      return 'shortDay';
    case 'tiny':
      return 'hidden';
    default:
      return mode satisfies never;
  }
}

/** Strip a leading star glyph from fixture/selector names when the row is starred. */
export function displayName(row: ResourceRowFields): string {
  const raw = row.name;
  if (!row.starred) {
    return raw;
  }
  return raw.replace(/^★\s*/, '');
}

function nameBudget(innerW: number): number {
  return Math.max(1, innerW - STAR_GUTTER_COLS);
}

const EMPTY_WIDTH_FALLBACKS = ['empty', '—'] as const;

/** Keep empty chrome on one line — shorten before truncate so narrow panes stay intentional. */
export function formatEmptyMessage(text: string, budget: number): string {
  const cols = Math.max(0, budget);
  if (cols === 0) {
    return '';
  }
  if (text.length <= cols) {
    return text;
  }
  for (const fallback of EMPTY_WIDTH_FALLBACKS) {
    if (fallback.length <= cols) {
      return fallback;
    }
  }
  if (cols <= 1) {
    return '…';
  }
  return `${text.slice(0, cols - 1)}…`;
}

/** Item title with scaled tree indent; keep ≥6 leading chars when truncated. */
export function formatItemTitle(name: string, budget: number, innerWidth?: number): string {
  return formatDocTreeName(name, innerWidth ?? budget, { maxLen: budget });
}

/** Parse `Mon. dd HH:MM` fixture strings and compress per disclosure stage. */
export function compressUpdatedAt(updatedAt: string, stage: DateCompress): string {
  if (stage === 'hidden') {
    return '';
  }
  const match = updatedAt.match(/^(\w{3})\.\s+(\d{1,2})(?:\s+(\d{2}:\d{2}))?$/);
  if (!match) {
    return updatedAt;
  }
  const [, mon, day, time] = match;
  switch (stage) {
    case 'full':
      return time === undefined ? updatedAt : `${mon}. ${day} ${time}`;
    case 'monthDay':
      return `${mon}. ${day}`;
    case 'shortDay':
      return `${mon} ${day}`;
    case 'numeric': {
      const monthNum = MONTHS.indexOf(mon as (typeof MONTHS)[number]) + 1;
      return `${monthNum}/${Number(day)}`;
    }
    default:
      return stage satisfies never;
  }
}

function formatMetaLine(
  row: ResourceRowFields,
  mode: ReportsDisplayMode,
  budget: number,
): string {
  const dateStage = dateCompressForMode(mode);
  const date = compressUpdatedAt(row.updatedAt, dateStage);
  if (!showCharCount(mode)) {
    return date;
  }
  const sep = ' · ';
  const minDate = dateStage === 'numeric' ? 3 : Math.min(date.length, 6);
  const charBudget = Math.max(0, budget - sep.length - minDate);
  let charPart = row.charCount;
  if (charBudget < charPart.length) {
    charPart =
      charBudget <= 1
        ? ''
        : charBudget <= 3
          ? charPart.slice(0, charBudget)
          : `${charPart.slice(0, charBudget - 1)}…`;
  }
  if (charPart.length === 0) {
    return date;
  }
  if (date.length === 0) {
    return charPart;
  }
  const combined = `${charPart}${sep}${date}`;
  if (combined.length <= budget) {
    return combined;
  }
  return `${charPart}${sep}${date.slice(0, Math.max(minDate, budget - charPart.length - sep.length))}`;
}

function renderReportsEntry(
  row: ResourceRowFields,
  ctx: LedgerEntryContext,
  mode: ReportsDisplayMode,
  innerW: number,
): React.ReactNode {
  const star = row.starred ? '★' : ' ';
  const lines = linesPerEntryForMode(mode);
  if (lines === 1) {
    const title = formatItemTitle(displayName(row), nameBudget(innerW), innerW);
    return (
      <Box flexGrow={1} flexShrink={0}>
        <Text wrap="truncate">{`${star} ${title}`}</Text>
      </Box>
    );
  }
  const title = formatItemTitle(displayName(row), nameBudget(innerW), innerW);
  const meta = formatMetaLine(row, mode, innerW);
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${star} ${title}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {meta.length > 0 ? meta : ' '}
      </Text>
    </Box>
  );
}

export interface ReportsPanelProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly rows: readonly ResourceRowFields[];
  readonly cursor?: number;
  readonly emptyText?: string;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

export const ReportsPanel = memo(function ReportsPanel({
  width,
  height,
  focused,
  rows,
  cursor: cursorProp,
  emptyText = 'no reports',
  status = 'ready',
  error = null,
}: ReportsPanelProps): React.JSX.Element {
  const theme = useTheme();
  const padding = paneHorizontalPaddingForWidth(width);
  const displayMode = layout(width, height);
  const innerW = contentWidth(width);
  const innerH = contentHeight(height);
  const rowCount = rows.length;
  const cursor = cursorProp ?? Math.min(1, Math.max(rowCount - 1, 0));
  const linesPerEntry = linesPerEntryForMode(displayMode);
  const hasHeader = showColumnHeader(displayMode, innerH) && rowCount > 0;
  const win = computeWindow(rowCount, cursor, linesPerEntry, innerH, hasHeader);
  const overflowAbove = rowCount === 0 ? 0 : win.start;
  const overflowBelow = rowCount === 0 ? 0 : rowCount - win.end;

  const listBody = (() => {
    if (status === 'error') {
      return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
    }
    if (status === 'loading' && rowCount === 0) {
      return <Text dimColor>loading…</Text>;
    }
    if (rowCount === 0) {
      return (
        <Text dimColor wrap="truncate">
          {formatEmptyMessage(emptyText, innerW)}
        </Text>
      );
    }
    return (
      <Ledger
        rows={rows}
        cursor={cursor}
        focused={focused}
        linesPerEntry={linesPerEntry}
        minColumns={1}
        maxColumns={1}
        availableWidth={innerW}
        availableHeight={innerH}
        renderEntry={(row, ctx) => renderReportsEntry(row, ctx, displayMode, innerW)}
        {...(hasHeader
          ? { header: () => renderReportsHeader(displayMode, innerW, innerH) }
          : {})}
        rowKey={(row) => row.name}
      />
    );
  })();

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
        {listBody}
      </Pane>
    </Box>
  );
});
