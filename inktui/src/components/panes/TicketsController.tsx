import { memo, useCallback, useEffect, useMemo, useRef } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { type TicketRowView, useTicketsView } from '../../selectors/ticketsSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useTicketEditor } from '../TicketEditorMode.js';
import { MeasuredPaneFrame } from './shared/MeasuredPaneFrame.js';
import { useClampedCursor } from './shared/useClampedCursor.js';
import { TicketsSurface, type TicketsSurfaceRow } from './TicketsSurface.js';

type TicketsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'open';

type SurfaceStatus = 'ready' | 'loading' | 'error';

function surfaceStatus(status: 'idle' | 'loading' | 'ready' | 'error'): SurfaceStatus {
  return status === 'loading' || status === 'error' ? status : 'ready';
}

export function ticketsSurfaceRowsFromView(
  rows: readonly TicketRowView[],
): readonly TicketsSurfaceRow[] {
  return rows.map((row) => ({
    id: row.id,
    idCell: row.idCell,
    titleCell: row.titleCell,
    statusCell: row.statusCell,
    statusTone: row.statusTone,
    lastUpdateCell: row.lastUpdateCell,
    depsCell: row.depsCell,
    depsSatisfied: row.depsSatisfied,
    scheduleCell: row.scheduleCell,
    harnessCell: row.harnessCell,
    modelCell: row.modelCell,
    planCell: row.planCell,
    worktreeCell: row.worktreeCell,
  }));
}

export interface TicketsControllerProps {
  readonly presentation: PanePresentation;
}

export const TicketsController = memo(function TicketsController({
  presentation,
}: TicketsControllerProps): React.JSX.Element {
  const tickets = useAppStore((state) => state.tickets, shallow);
  const refresh = useAppStore((state) => state.actions.tickets.refresh);
  const view = useTicketsView(tickets);
  const theme = useTheme();
  const rows = useMemo(() => ticketsSurfaceRowsFromView(view.rows), [view.rows]);
  const openEditor = useTicketEditor();
  const { cursor, moveDown, moveUp } = useClampedCursor(rows.length);
  const cursorRef = useRef(cursor);
  const rowsRef = useRef(rows);
  cursorRef.current = cursor;
  rowsRef.current = rows;

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openAtCursor = useCallback(() => {
    const row = rowsRef.current[cursorRef.current];
    if (row !== undefined) {
      openEditor(row.id);
    }
  }, [openEditor]);

  const keymap: PanelKeymap<TicketsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next ticket',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev ticket',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { key: { return: true } }, intent: 'open', description: 'open ticket' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            moveDown();
            return;
          case 'cursorUp':
            moveUp();
            return;
          case 'refresh':
            void refresh();
            return;
          case 'open':
            openAtCursor();
            return;
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveDown, moveUp, openAtCursor, refresh],
  );
  usePanelKeymap('tickets', keymap);

  return (
    <MeasuredPaneFrame id="tickets" presentation={presentation}>
      <TicketsSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={rows}
        cursor={cursor}
        status={surfaceStatus(view.status)}
        error={view.error}
      />
    </MeasuredPaneFrame>
  );
});
