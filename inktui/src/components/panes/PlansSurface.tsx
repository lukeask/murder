/**
 * PlansSurface — store-free, dimension-driven plans list for fixtures and the new pane contract.
 *
 * Accepts explicit `width`/`height` (full allocation including border, title, footer, padding).
 * A local layout router picks a display mode; rendering branches on that mode only. Matches the old
 * {@link ../PlansPanel.tsx} doc-panel intent at large sizes (two-line ResourceRow entries, tree
 * indent baked into `row.name` by the selector).
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { claimMouseClick } from '../../input/mouseClick.js';
import type { Theme } from '../../theme/buildTheme.js';
import { computeWindow, Ledger } from '../Ledger.js';
import { Pane, paneContentWidthForWidth, paneHorizontalPaddingForWidth } from '../Pane.js';
import type { ResourceRowFields } from '../ResourceRow.js';
import {
  renderPlansEntry,
  renderPlansHeader,
  rowLayoutForDimensions,
} from './plansPanelDocList.js';
import { CreateRow } from './shared/CreateRow.js';
import { dataIndexFromCursor, isCreateCursor } from './shared/createListRow.js';

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

export interface PlansSurfaceProps {
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

export const PlansSurface = memo(function PlansSurface({
  width,
  height,
  focused,
  theme,
  rows,
  cursor: cursorProp,
  emptyText = 'no plans',
  createLabel,
  status = 'ready',
  error = null,
  onRowClick,
}: PlansSurfaceProps): React.JSX.Element {
  const padding = paneHorizontalPaddingForWidth(width);
  const innerW = innerWidth(width);
  const innerH = innerHeight(height);
  const rowLayout = useMemo(() => rowLayoutForDimensions(innerW, innerH), [innerW, innerH]);
  const showCreate = createLabel !== undefined;
  const dataCount = rows.length;
  const totalCount = showCreate ? dataCount + 1 : dataCount;
  const cursor = cursorProp ?? Math.min(1, Math.max(totalCount - 1, 0));
  const createSelected = showCreate && focused && isCreateCursor(cursor);
  const ledgerCursor = showCreate ? (dataIndexFromCursor(cursor) ?? -1) : cursor;
  const ledgerHeight = showCreate ? Math.max(1, innerH - 1) : innerH;
  const hasHeader = rowLayout.showHeader && dataCount > 0;
  const win = computeWindow(
    dataCount,
    Math.max(0, ledgerCursor),
    rowLayout.linesPerEntry,
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
      return <Text color={theme.muted}>{emptyText}</Text>;
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
            linesPerEntry={rowLayout.linesPerEntry}
            minColumns={1}
            maxColumns={1}
            availableWidth={innerW}
            availableHeight={ledgerHeight}
            renderEntry={(row, ctx) => renderPlansEntry(row, ctx, innerW, rowLayout, theme)}
            {...(hasHeader ? { header: () => renderPlansHeader(rowLayout, theme) } : {})}
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
