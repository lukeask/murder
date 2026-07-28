import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import {
  type WorkflowPanelRowView,
  useWorkflowsPanelView,
} from '../../selectors/workflowsPanelSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useTicketEditor } from '../TicketEditorMode.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { usePaneUiClampedCursor } from './shared/useClampedCursor.js';
import { WorkflowsSurface, type WorkflowsSurfaceRow } from './WorkflowsSurface.js';

type WorkflowsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'open';

type SurfaceStatus = 'ready' | 'loading' | 'error';

function surfaceStatus(status: 'idle' | 'loading' | 'ready' | 'error'): SurfaceStatus {
  return status === 'loading' || status === 'error' ? status : 'ready';
}

export function workflowsSurfaceRowsFromView(
  rows: readonly WorkflowPanelRowView[],
): readonly WorkflowsSurfaceRow[] {
  return rows.map((row) => ({
    id: row.id,
    kind: row.kind,
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

export interface WorkflowsControllerProps {
  readonly presentation: PanePresentation;
}

export const WorkflowsController = memo(function WorkflowsController({
  presentation,
}: WorkflowsControllerProps): React.JSX.Element {
  const workflowRuns = useAppStore((state) => state.workflowRuns, shallow);
  const tickets = useAppStore((state) => state.tickets, shallow);
  const refreshList = useAppStore((state) => state.actions.workflowRuns.refreshList);
  const refreshTickets = useAppStore((state) => state.actions.tickets.refresh);
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<ReadonlySet<string>>(() => new Set());
  const view = useWorkflowsPanelView(workflowRuns, tickets, collapsedGroupIds);
  const theme = useTheme();
  const rows = useMemo(() => workflowsSurfaceRowsFromView(view.rows), [view.rows]);
  const openEditor = useTicketEditor();
  const { cursor, setCursor, moveDown, moveUp } = usePaneUiClampedCursor('workflows', rows.length);
  const cursorRef = useRef(cursor);
  const viewRowsRef = useRef(view.rows);
  cursorRef.current = cursor;
  viewRowsRef.current = view.rows;

  const refresh = useCallback(() => {
    void refreshList();
    void refreshTickets();
  }, [refreshList, refreshTickets]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggleGroup = useCallback((groupId: string) => {
    setCollapsedGroupIds((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }, []);

  const activateRow = useCallback(
    (row: WorkflowPanelRowView | undefined) => {
      if (row === undefined) {
        return;
      }
      if (row.kind === 'run' && row.groupId !== null) {
        toggleGroup(row.groupId);
        return;
      }
      if (row.openTicketId !== null) {
        openEditor(row.openTicketId);
      }
    },
    [openEditor, toggleGroup],
  );

  const openAtCursor = useCallback(() => {
    activateRow(viewRowsRef.current[cursorRef.current]);
  }, [activateRow]);

  const onRowClick = useCallback(
    (index: number) => {
      setCursor(index);
      activateRow(viewRowsRef.current[index]);
    },
    [activateRow, setCursor],
  );

  const keymap: PanelKeymap<WorkflowsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next row',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev row',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        {
          chord: { key: { return: true } },
          intent: 'open',
          description: 'open node / toggle run',
        },
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
            refresh();
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
  usePanelKeymap('workflows', keymap);

  return (
    <AllocatedPaneFrame id="workflows" presentation={presentation}>
      <WorkflowsSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={rows}
        cursor={cursor}
        status={surfaceStatus(view.status)}
        error={view.error}
        onRowClick={onRowClick}
      />
    </AllocatedPaneFrame>
  );
});
