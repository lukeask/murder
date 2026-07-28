import { describe, expect, it } from 'vitest';
import { selectWorkflowPanelRows, selectWorkflowsPanelView } from '../../src/selectors/workflowsPanelSelectors.js';
import type { TicketRow } from '../../src/store/tickets/ticketsSlice.js';
import type { WorkflowRunListItem, WorkflowRunsState } from '../../src/store/workflowRuns/workflowRunsSlice.js';
import { initialWorkflowRunsState } from '../../src/store/workflowRuns/workflowRunsSlice.js';
import { initialTicketsState } from '../../src/store/tickets/ticketsSlice.js';

function ticket(overrides: Partial<TicketRow> & Pick<TicketRow, 'id'>): TicketRow {
  return {
    title: overrides.title ?? overrides.id,
    status: 'ready',
    lastUpdateAt: '2026-07-28T00:00:00Z',
    lastUpdateLabel: 'now',
    scheduleAt: null,
    harness: null,
    model: null,
    pendingDepIds: [],
    parent: null,
    ...overrides,
  };
}

function run(overrides: Partial<WorkflowRunListItem> = {}): WorkflowRunListItem {
  return {
    workflow_id: 'wf-1',
    definition_name: 'review',
    definition_version: 2,
    status: 'running',
    revision: 1,
    state: { schema_name: 'static_dag', schema_version: 1, value: {} },
    created_at: '2026-07-28T01:00:00Z',
    updated_at: '2026-07-28T01:05:00Z',
    started_by: { kind: 'user', id: 'u' },
    correlation: { correlation_id: 'c' },
    parent_ticket_id: 'T-parent',
    definition_snapshot: {
      name: 'review',
      stages: [
        { id: 'a', title: 'Stage A' },
        { id: 'b', title: 'Stage B' },
      ],
    },
    stage_map: { a: 'T-a', b: 'T-b' },
    ...overrides,
  };
}

describe('selectWorkflowPanelRows', () => {
  it('emits run headers with stage_map nodes and hides the parent ticket', () => {
    const rows = selectWorkflowPanelRows(
      [run()],
      [
        ticket({ id: 'T-parent', title: 'parent run ticket' }),
        ticket({ id: 'T-a', title: 'Alpha', status: 'in_progress' }),
        ticket({ id: 'T-b', title: 'Beta', status: 'ready' }),
      ],
    );

    expect(rows.map((r) => r.kind)).toEqual(['run', 'node', 'node']);
    expect(rows[0]).toMatchObject({
      kind: 'run',
      workflowId: 'wf-1',
      templateName: 'review',
      templateVersion: 2,
      nodeCount: 2,
    });
    expect(rows[1]).toMatchObject({ kind: 'node', stageId: 'a', ticketId: 'T-a', title: 'Alpha' });
    expect(rows[2]).toMatchObject({ kind: 'node', stageId: 'b', ticketId: 'T-b', title: 'Beta' });
  });

  it('collapses node rows under a run when the group is collapsed', () => {
    const rows = selectWorkflowPanelRows([run()], [ticket({ id: 'T-a' }), ticket({ id: 'T-b' })], new Set(['wf-1']));
    expect(rows).toHaveLength(1);
    expect(rows[0]?.kind).toBe('run');
  });

  it('synthesizes legacy ticket runs for standalone tickets', () => {
    const rows = selectWorkflowPanelRows(
      [run()],
      [
        ticket({ id: 'T-parent' }),
        ticket({ id: 'T-a' }),
        ticket({ id: 'T-b' }),
        ticket({ id: 'T-solo', title: 'Standalone', lastUpdateAt: '2026-07-29T00:00:00Z' }),
      ],
    );
    const legacy = rows.filter((r) => r.kind === 'legacy-ticket-run');
    expect(legacy).toEqual([
      expect.objectContaining({
        kind: 'legacy-ticket-run',
        syntheticId: 'ticket:T-solo',
        templateName: 'Ticket',
        ticketId: 'T-solo',
        title: 'Standalone',
      }),
    ]);
  });
});

describe('selectWorkflowsPanelView', () => {
  it('maps domain rows into display cells and opens ticket ids for nodes', () => {
    const workflowRuns: WorkflowRunsState = {
      ...initialWorkflowRunsState,
      runs: [run()],
      listStatus: 'ready',
    };
    const tickets = {
      ...initialTicketsState,
      rows: [ticket({ id: 'T-a', title: 'Alpha' }), ticket({ id: 'T-b', title: 'Beta' })],
      status: 'ready' as const,
    };
    const view = selectWorkflowsPanelView(workflowRuns, tickets);
    expect(view.isEmpty).toBe(false);
    expect(view.rows[0]).toMatchObject({
      kind: 'run',
      openTicketId: null,
      groupId: 'wf-1',
      idCell: 'review',
    });
    expect(view.rows[1]).toMatchObject({
      kind: 'node',
      openTicketId: 'T-a',
      idCell: 'T-a',
    });
  });
});
