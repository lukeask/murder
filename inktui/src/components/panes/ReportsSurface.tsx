/**
 * ReportsSurface — store-free, dimension-driven reports list for fixtures and the new pane contract.
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only. Doc rows reserve
 * a star column, degrade date/char-count metadata before the item title, and keep ≥6 name prefix
 * chars when truncated.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { claimMouseClick } from '@murder/ui-core/input/mouseClick.js';
import type { Theme } from '@murder/ui-core/theme/buildTheme.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import type { ResourceRowFields } from '../ResourceRow.js';
import { formatDocTreeName } from './docTreeIndent.js';
import { CreateRow } from './shared/CreateRow.js';
import { dataIndexFromCursor, isCreateCursor } from './shared/createListRow.js';

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
  theme: Theme,
): React.ReactNode {
  if (!showColumnHeader(mode, innerH)) {
    return null;
  }
  if (mode === 'full' && innerH >= 9) {
    const line2 = headerShowsSize(mode, innerW) ? 'size · updated' : 'updated';
    if (line2.length <= innerW) {
      return (
        <Box flexDirection="column" flexShrink={0}>
          <Text color={theme.muted}>{'name'}</Text>
          <Text color={theme.muted}>{line2}</Text>
        </Box>
      );
    }
  }
  if (innerW >= 4) {
    return (
      <Box flexShrink={0}>
        <Text color={theme.muted}>{'name'}</Text>
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

function formatMetaLine(row: ResourceRowFields, mode: ReportsDisplayMode, budget: number): string {
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
  theme: Theme,
): React.ReactNode {
  const star = row.starred ? '★' : ' ';
  const lines = linesPerEntryForMode(mode);
  if (lines === 1) {
    const title = formatItemTitle(displayName(row), nameBudget(innerW), innerW);
    return (
      <Box flexGrow={1} flexShrink={0}>
        <Text color={theme.text} wrap="truncate">{`${star} ${title}`}</Text>
      </Box>
    );
  }
  const title = formatItemTitle(displayName(row), nameBudget(innerW), innerW);
  const meta = formatMetaLine(row, mode, innerW);
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text color={theme.text} wrap="truncate">{`${star} ${title}`}</Text>
      <Text color={ctx.selected ? theme.text : theme.muted} wrap="truncate">
        {meta.length > 0 ? meta : ' '}
      </Text>
    </Box>
  );
}

export interface ReportsSurfaceProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly theme: Theme;
  readonly rows: readonly ResourceRowFields[];
  readonly cursor?: number;
  readonly emptyText?: string;
  /** When set, a 1-line “+ create” row is always shown above the ledger (index 0). */
  readonly createLabel?: string;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
  /** Left-click a list row (absolute index, including create at 0 when createLabel is set). */
  readonly onRowClick?: (index: number) => void;
}

export const ReportsSurface = memo(function ReportsSurface({
  width,
  height,
  focused,
  theme,
  rows,
  cursor: cursorProp,
  emptyText = 'no reports',
  createLabel,
  status = 'ready',
  error = null,
  onRowClick,
}: ReportsSurfaceProps): React.JSX.Element {
  const padding = paneHorizontalPaddingForWidth(width);
  const displayMode = layout(width, height);
  const innerW = contentWidth(width);
  const innerH = contentHeight(height);
  const showCreate = createLabel !== undefined;
  const dataCount = rows.length;
  const totalCount = showCreate ? dataCount + 1 : dataCount;
  const cursor = cursorProp ?? Math.min(1, Math.max(totalCount - 1, 0));
  const createSelected = showCreate && focused && isCreateCursor(cursor);
  const ledgerCursor = showCreate ? (dataIndexFromCursor(cursor) ?? -1) : cursor;
  const ledgerHeight = showCreate ? Math.max(1, innerH - 1) : innerH;
  const linesPerEntry = linesPerEntryForMode(displayMode);
  const hasHeader = showColumnHeader(displayMode, innerH) && dataCount > 0;
  const win = computeWindow(
    dataCount,
    Math.max(0, ledgerCursor),
    linesPerEntry,
    ledgerHeight,
    hasHeader,
  );
  const overflowAbove = dataCount === 0 ? 0 : win.start;
  const overflowBelow = dataCount === 0 ? 0 : dataCount - win.end;

  const listBody = (() => {
    if (status === 'error') {
      return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
    }
    if (status === 'loading' && dataCount === 0 && !showCreate) {
      return <Text color={theme.muted}>loading…</Text>;
    }
    if (dataCount === 0 && !showCreate) {
      return (
        <Text color={theme.muted} wrap="truncate">
          {formatEmptyMessage(emptyText, innerW)}
        </Text>
      );
    }
    return (
      <Box flexDirection="column" flexGrow={1} overflow="hidden">
        {showCreate && createLabel !== undefined ? (
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
        ) : null}
        {status === 'loading' && dataCount === 0 ? (
          <Text color={theme.muted}>loading…</Text>
        ) : dataCount === 0 ? null : (
          <Ledger
            rows={rows}
            cursor={ledgerCursor}
            focused={focused && !createSelected}
            linesPerEntry={linesPerEntry}
            minColumns={1}
            maxColumns={1}
            availableWidth={innerW}
            availableHeight={ledgerHeight}
            renderEntry={(row, ctx) => renderReportsEntry(row, ctx, displayMode, innerW, theme)}
            {...(hasHeader
              ? { header: () => renderReportsHeader(displayMode, innerW, innerH, theme) }
              : {})}
            rowKey={(row) => row.name}
            {...(onRowClick !== undefined
              ? {
                  onRowClick: (_row: ResourceRowFields, index: number, event) => {
                    claimMouseClick(event);
                    onRowClick(showCreate ? index + 1 : index);
                  },
                }
              : {})}
          />
        )}
      </Box>
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
