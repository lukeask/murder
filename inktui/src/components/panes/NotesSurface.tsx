/**
 * NotesSurface — store-free, dimension-driven notes list for fixtures and the new pane contract.
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only. Matches the old
 * {@link ../NotesPanel.tsx} doc-panel intent at large sizes (two-line entries, starred sort baked
 * into `row.starred` before render).
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import type { Theme } from '../../theme/buildTheme.js';
import { computeWindow, Ledger, type LedgerEntryContext } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import type { ResourceRowFields } from '../ResourceRow.js';
import {
  formatDocTreeName,
  MIN_TITLE_PREFIX,
  parseTreeName,
  tabLenForWidth,
} from './docTreeIndent.js';

const PANEL_TITLE = 'Notes';

/** Deterministic presentation modes — richest first. */
export type NotesDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

/** Title row plus bottom border row reserved outside the ledger budget. */
const CHROME_ROWS = 2;

/** Reserved star column: `★ ` when starred, two spaces when not. */
const STAR_COL_WIDTH = 2;
const MONTH_NUM: Record<string, number> = {
  'Jan.': 1,
  'Feb.': 2,
  'Mar.': 3,
  'Apr.': 4,
  'May.': 5,
  'Jun.': 6,
  'Jul.': 7,
  'Aug.': 8,
  'Sep.': 9,
  'Oct.': 10,
  'Nov.': 11,
  'Dec.': 12,
};

const MONTH_SHORT = [
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

function contentWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function contentHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

/**
 * Deterministic size router — width drives metadata disclosure; height gates compact/full and
 * reserves tiny for allocations too narrow to list. Phase 1 width + Phase 2 height thresholds.
 */
export function layout(width: number, height: number): NotesDisplayMode {
  const innerW = contentWidth(width);
  const innerH = contentHeight(height);
  if (innerH < 3 || innerW < 8) {
    return 'tiny';
  }
  if (innerW < 12) {
    return 'minimal';
  }
  if (innerH < 5 || innerW < 18) {
    return 'minimal';
  }
  if (innerW < 24) {
    return 'compact';
  }
  if (innerH < 8) {
    return 'compact';
  }
  return 'full';
}

function starCell(starred: boolean): string {
  return starred ? '★ ' : '  ';
}

const NO_TREE_INDENT = {
  wideTabLen: 0,
  defaultTabLen: 0,
  minTabLen: 0,
} as const;

/** Item title with scaled tree indent; keep ≥6 leading chars when truncated. */
export function formatItemName(name: string, budget: number, innerWidth?: number): string {
  const iw = innerWidth ?? budget;
  const { depth, title } = parseTreeName(name);
  if (depth > 0 && title.length > MIN_TITLE_PREFIX) {
    const indentLen = depth * tabLenForWidth(iw);
    if (budget - indentLen < MIN_TITLE_PREFIX) {
      return formatDocTreeName(name, iw, { ...NO_TREE_INDENT, maxLen: budget });
    }
  }
  return formatDocTreeName(name, iw, { maxLen: budget });
}

function dateVariants(updatedAt: string): readonly string[] {
  const match = /^(\w+\.)\s+(\d+)\s+(.+)$/.exec(updatedAt);
  if (match === null) {
    return [updatedAt];
  }
  const monthKey = match[1];
  const day = match[2];
  if (monthKey === undefined || day === undefined) {
    return [updatedAt];
  }
  const month = MONTH_NUM[monthKey];
  if (month === undefined) {
    return [updatedAt];
  }
  const short = MONTH_SHORT[month - 1];
  return [updatedAt, `${monthKey} ${day}`, `${short} ${day}`, `${month}/${day}`];
}

/** Line-2 metadata: truncate char count before date; compress date; drop char count last. */
export function formatMetaLine(
  charCount: string,
  updatedAt: string,
  budget: number,
): { readonly text: string; readonly showsCharCount: boolean } {
  if (budget <= 0) {
    return { text: '', showsCharCount: false };
  }
  const dates = dateVariants(updatedAt);
  const sep = ' · ';

  for (let dateIndex = 0; dateIndex < dates.length; dateIndex += 1) {
    const date = dates[dateIndex] ?? updatedAt;
    const withCount = `${charCount}${sep}${date}`;
    if (withCount.length <= budget) {
      return { text: withCount, showsCharCount: true };
    }
    const maxCc = budget - sep.length - date.length;
    if (maxCc > 0) {
      const clipped =
        charCount.length <= maxCc ? charCount : charCount.slice(0, Math.max(1, maxCc));
      const partial = `${clipped}${sep}${date}`;
      if (partial.length <= budget) {
        return { text: partial, showsCharCount: true };
      }
    }
  }

  for (let dateIndex = dates.length - 1; dateIndex >= 0; dateIndex -= 1) {
    const date = dates[dateIndex] ?? updatedAt;
    if (date.length <= budget) {
      return { text: date, showsCharCount: false };
    }
  }
  const fallback = dates[dates.length - 1] ?? updatedAt;
  return { text: fallback.slice(0, budget), showsCharCount: false };
}

function linesPerEntryForMode(mode: NotesDisplayMode): number {
  return mode === 'minimal' || mode === 'tiny' ? 1 : 2;
}

function showColumnHeader(mode: NotesDisplayMode, innerH: number): boolean {
  if (mode === 'minimal' || mode === 'tiny' || innerH < 6) {
    return false;
  }
  return mode === 'full' || mode === 'compact';
}

function headerShowsSize(mode: NotesDisplayMode, innerW: number): boolean {
  if (mode !== 'full') {
    return false;
  }
  const sample = formatMetaLine('12.4k', 'Jun. 21 09:32', innerW);
  return sample.showsCharCount;
}

function renderNotesHeader(
  mode: NotesDisplayMode,
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
          <Text dimColor>name</Text>
          <Text dimColor>{line2}</Text>
        </Box>
      );
    }
  }
  if (innerW >= 4) {
    return (
      <Box flexShrink={0}>
        <Text dimColor>name</Text>
      </Box>
    );
  }
  return null;
}

function renderNotesEntry(
  row: ResourceRowFields,
  ctx: LedgerEntryContext,
  mode: NotesDisplayMode,
  innerW: number,
): React.ReactNode {
  const star = starCell(row.starred);
  const nameBudget = Math.max(0, innerW - STAR_COL_WIDTH);
  const name = formatItemName(row.name, nameBudget, innerW);

  if (mode === 'minimal' || mode === 'tiny') {
    return (
      <Box flexGrow={1} flexShrink={0}>
        <Text wrap="truncate">{`${star}${name}`}</Text>
      </Box>
    );
  }

  const meta = formatMetaLine(row.charCount, row.updatedAt, innerW);
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${star}${name}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {meta.text}
      </Text>
    </Box>
  );
}

export interface NotesSurfaceProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly theme: Theme;
  readonly rows: readonly ResourceRowFields[];
  readonly cursor?: number;
  readonly emptyText?: string;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

export const NotesSurface = memo(function NotesSurface({
  width,
  height,
  focused,
  theme,
  rows,
  cursor: cursorProp,
  emptyText = 'no notes',
  status = 'ready',
  error = null,
}: NotesSurfaceProps): React.JSX.Element {
  const padding = paneHorizontalPaddingForWidth(width);
  const mode = layout(width, height);
  const innerW = contentWidth(width);
  const innerH = contentHeight(height);
  const rowCount = rows.length;
  const cursor = cursorProp ?? Math.min(1, Math.max(rowCount - 1, 0));
  const linesPerEntry = linesPerEntryForMode(mode);
  const hasHeader = showColumnHeader(mode, innerH) && rowCount > 0;
  const win = computeWindow(rowCount, cursor, linesPerEntry, innerH, hasHeader);
  const overflowAbove = rowCount === 0 ? 0 : win.start;
  const overflowBelow = rowCount === 0 ? 0 : rowCount - win.end;

  const listBody = (() => {
    if (status === 'error') {
      return <Text color={theme.error}>{`error: ${error ?? 'unknown'} (r to retry)`}</Text>;
    }
    if (status === 'loading' && rowCount === 0) {
      return (
        <Text dimColor wrap="truncate">
          {formatEmptyMessage('loading…', innerW)}
        </Text>
      );
    }
    if (rowCount === 0) {
      return (
        <Text dimColor wrap="truncate">
          {formatEmptyMessage(emptyText, innerW)}
        </Text>
      );
    }
    if (mode === 'tiny') {
      const row = rows[Math.min(cursor, rowCount - 1)];
      const star = row === undefined ? '' : starCell(row.starred);
      const name =
        row === undefined
          ? emptyText
          : formatItemName(row.name, Math.max(0, innerW - star.length), innerW);
      return <Text wrap="truncate">{row === undefined ? emptyText : `${star}${name}`}</Text>;
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
        renderEntry={(row, ctx) => renderNotesEntry(row, ctx, mode, innerW)}
        {...(hasHeader ? { header: () => renderNotesHeader(mode, innerW, innerH) } : {})}
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
