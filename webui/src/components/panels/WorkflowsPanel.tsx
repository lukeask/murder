/**
 * WorkflowsPanel — run-first hierarchical list over `workflowRuns` + `tickets` via
 * {@link useWorkflowsPanelView}. Run headers expand/collapse stage nodes; ticket/node rows open
 * detail via `ticketDetail.open`. “+ new workflow” opens the template library.
 * Keyboard (when focused): j/k, Enter expand/open, r refresh.
 */

import {
  type WorkflowPanelRowView,
  useWorkflowsPanelView,
} from '@murder/ui-core/selectors/workflowsPanelSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useCallback, useEffect, useState } from 'react';
import { usePaneUiClampedCursor } from '../../composer/usePaneUiClampedCursor.js';
import { useCreationDialogs } from '../../creationDialogs.js';
import { panelFocusStore, useIsPanelFocused } from '../../panelFocus.js';
import { usePanelListKeys } from '../../usePanelListKeys.js';
import { Panel, ListRow, Badge, Tag, IconButton, Icon, cx } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

function activateRow(
  row: WorkflowPanelRowView,
  toggleGroup: (groupId: string) => void,
  openDetail: (ticketId: string) => void,
): void {
  if (row.kind === 'run' && row.groupId !== null) {
    toggleGroup(row.groupId);
    return;
  }
  if (row.openTicketId !== null) {
    void openDetail(row.openTicketId);
  }
}

export function WorkflowsPanel({
  onNewWorkflow,
}: {
  /** Override the default “open template library” action (tests). */
  readonly onNewWorkflow?: () => void;
} = {}): React.JSX.Element {
  const { openWorkflowLibrary } = useCreationDialogs();
  const handleNewWorkflow = onNewWorkflow ?? (() => openWorkflowLibrary());
  const workflowRuns = useAppStore((s) => s.workflowRuns, shallow);
  const tickets = useAppStore((s) => s.tickets, shallow);
  const refreshList = useAppStore((s) => s.actions.workflowRuns.refreshList);
  const refreshTickets = useAppStore((s) => s.actions.tickets.refresh);
  const openDetail = useAppStore((s) => s.actions.ticketDetail.open);
  const openId = useAppStore((s) => s.ticketDetail.ticketId);
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<ReadonlySet<string>>(() => new Set());
  const view = useWorkflowsPanelView(workflowRuns, tickets, collapsedGroupIds);
  const focused = useIsPanelFocused('workflows');
  const [cursor, setCursor] = usePaneUiClampedCursor('workflows', view.rows.length);

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

  usePanelListKeys({
    active: focused,
    itemCount: view.rows.length,
    cursor,
    setCursor,
    onActivate: () => {
      const row = view.rows[cursor];
      if (row !== undefined) activateRow(row, toggleGroup, openDetail);
    },
    onAction: (key) => {
      if (key === 'r') {
        refresh();
        return true;
      }
      return false;
    },
  });

  return (
    <Panel
      title="workflows"
      count={view.isEmpty ? null : view.rows.length}
      flush
      active={focused}
      data-panel-id="workflows"
      onHeaderClick={() => panelFocusStore.getState().focus('workflows')}
      actions={
        <IconButton label="New workflow" onClick={handleNewWorkflow}>
          <Icon name="plus" size={14} />
        </IconButton>
      }
    >
      <SliceHint state={view} empty="No workflows." />
      {view.rows.map((row, index) => {
        const isRun = row.kind === 'run';
        const collapsed = row.groupId !== null && collapsedGroupIds.has(row.groupId);
        const primaryTitle = isRun ? row.idCell : row.titleCell.trimStart();
        const selected =
          (focused && index === cursor) ||
          (!focused && row.openTicketId !== null && row.openTicketId === openId);
        return (
          <ListRow
            key={row.id}
            className={cx('workflow-row', row.depth > 0 && 'workflow-row--child')}
            selected={selected}
            onClick={() => {
              panelFocusStore.getState().focus('workflows');
              setCursor(index);
              activateRow(row, toggleGroup, openDetail);
            }}
            style={
              row.depth > 0
                ? { paddingLeft: `calc(var(--space-3) + ${row.depth} * var(--space-4))` }
                : undefined
            }
            title={
              <span className="workflow-row__label">
                {isRun ? (
                  <Icon
                    name="chevron-down"
                    size={14}
                    className={cx(
                      'workflow-row__chevron',
                      collapsed && 'workflow-row__chevron--collapsed',
                    )}
                  />
                ) : (
                  <span className="workflow-row__chevron-spacer" aria-hidden="true" />
                )}
                <span className="workflow-row__title-text">{primaryTitle}</span>
              </span>
            }
            meta={
              <span className="ticket-meta">
                {!isRun ? <span className="ticket-meta__id">{row.idCell}</span> : null}
                {isRun ? <span className="ticket-meta__cell">{row.titleCell}</span> : null}
                <span className="ticket-meta__cell">{row.lastUpdateCell}</span>
                <span
                  className={
                    row.depsSatisfied
                      ? 'ticket-meta__cell tone--success'
                      : 'ticket-meta__cell tone--warning'
                  }
                >
                  {row.depsCell}
                </span>
                <span className="ticket-meta__cell">{row.scheduleCell}</span>
                {row.harnessCell !== '—' && row.harnessCell.length > 0 ? (
                  <Tag>{row.harnessCell}</Tag>
                ) : null}
              </span>
            }
            trailing={
              <Badge tone={row.statusTone} dot>
                {row.statusCell}
              </Badge>
            }
          />
        );
      })}
    </Panel>
  );
}
