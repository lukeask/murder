/**
 * PlansPanel — store-free, dimension-driven plans list for fixtures and the new pane contract.
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only. Matches the old
 * {@link ../PlansPanel.tsx} doc-panel intent at large sizes (two-line ResourceRow entries, tree
 * indent baked into `row.name` by the selector).
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { useTheme } from '../../theme/themeStore.js';
import { computeWindow, Ledger } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import type { ResourceRowFields } from '../ResourceRow.js';
import {
  renderPlansEntry,
  renderPlansHeader,
  rowLayoutForDimensions,
} from './plansPanelDocList.js';

const PANEL_TITLE = 'Plans';

/** Deterministic presentation modes — richest first. */
export type PlansDisplayMode = 'full' | 'compact' | 'narrow' | 'minimal' | 'tiny';

/** Title row plus bottom border row reserved outside the ledger budget. */
const CHROME_ROWS = 2;

/**
 * Deterministic size router — width drives metadata disclosure; height gates two-line rows,
 * column header, and tiny fallback (Phase 2 tuned at mixed fixture widths).
 */
export function layout(width: number, height: number): PlansDisplayMode {
  const w = innerWidth(width);
  const h = innerHeight(height);
  if (h < 4 || w < 6) {
    return 'tiny';
  }
  if (w < 8 && h < 5) {
    return 'tiny';
  }
  if (w < 10) {
    return 'minimal';
  }
  if (w < 14) {
    return 'minimal';
  }
  if (h < 6 || w < 20) {
    return 'narrow';
  }
  if (h < 8 || w < 26) {
    return 'compact';
  }
  return 'full';
}

function innerWidth(width: number): number {
  return paneContentWidthForWidth(width);
}

function innerHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

export interface PlansPanelProps {
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

export const PlansPanel = memo(function PlansPanel({
  width,
  height,
  focused,
  rows,
  cursor: cursorProp,
  emptyText = 'no plans',
  status = 'ready',
  error = null,
}: PlansPanelProps): React.JSX.Element {
  const theme = useTheme();
  const padding = paneHorizontalPaddingForWidth(width);
  const innerW = innerWidth(width);
  const innerH = innerHeight(height);
  const rowLayout = useMemo(
    () => rowLayoutForDimensions(innerW, innerH),
    [innerW, innerH],
  );
  const rowCount = rows.length;
  const cursor = cursorProp ?? Math.min(1, Math.max(rowCount - 1, 0));
  const hasHeader = rowLayout.showHeader && rowCount > 0;
  const win = computeWindow(rowCount, cursor, rowLayout.linesPerEntry, innerH, hasHeader);
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
      return <Text dimColor>{emptyText}</Text>;
    }
    return (
      <Ledger
        rows={rows}
        cursor={cursor}
        focused={focused}
        linesPerEntry={rowLayout.linesPerEntry}
        minColumns={1}
        maxColumns={1}
        availableWidth={innerW}
        availableHeight={innerH}
        renderEntry={(row, ctx) => renderPlansEntry(row, ctx, innerW, rowLayout)}
        {...(hasHeader ? { header: () => renderPlansHeader(rowLayout) } : {})}
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
