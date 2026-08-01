/**
 * WorkflowsPanel renders the run-first tree from `workflowRuns` + `tickets` (via mount refresh
 * off FakeApplicationClient stubs). Asserts Panel chrome, run/node rows, expand/collapse,
 * ticket detail open, and the new-workflow stub callback.
 */

import type { WorkflowRunListItem } from '@murder/ui-core/store/workflowRuns/workflowRunsSlice.js';
import type { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkflowsPanel } from '../src/components/panels/WorkflowsPanel.js';
import { makeStore, renderWithStore } from './helpers.js';

afterEach(cleanup);

const run = (over: Partial<WorkflowRunListItem> = {}): WorkflowRunListItem => ({
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
  ...over,
});

function ticketDto(
  id: string,
  title: string,
  status = 'ready',
): {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  readonly last_update_at: string;
  readonly last_update_label: string;
  readonly pending_dep_ids: readonly string[];
  readonly harness: string;
  readonly model: string;
} {
  return {
    id,
    title,
    status,
    last_update_at: '2026-07-28T00:00:00Z',
    last_update_label: 'now',
    pending_dep_ids: [],
    harness: 'claude',
    model: 'opus',
  };
}

function stubWorkflowTree(bus: FakeApplicationClient): void {
  bus.stubQuery('workflow.runs.list', { runs: [run()] });
  bus.stubQuery('schedule.get', {
    active_tickets: [
      ticketDto('T-parent', 'parent run ticket'),
      ticketDto('T-a', 'Alpha', 'in_progress'),
      ticketDto('T-b', 'Beta'),
    ],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
    invalidation_key: 'k',
  });
}

describe('WorkflowsPanel', () => {
  it('renders run header and stage node rows', async () => {
    const { store, bus } = makeStore();
    stubWorkflowTree(bus);
    renderWithStore(<WorkflowsPanel />, { store, bus });

    expect(document.querySelector('[data-panel-id="workflows"]')).toBeTruthy();
    expect(screen.getByText('workflows')).toBeTruthy();
    await waitFor(() => expect(screen.getByText('review')).toBeTruthy());
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(document.querySelectorAll('.mds-row').length).toBe(3);
    expect(document.querySelector('.workflow-row__chevron')).toBeTruthy();
  });

  it('collapses node rows when the run header is activated', async () => {
    const { store, bus } = makeStore();
    stubWorkflowTree(bus);
    renderWithStore(<WorkflowsPanel />, { store, bus });

    await waitFor(() => expect(screen.getByText('review')).toBeTruthy());
    fireEvent.click(screen.getByText('review'));
    expect(screen.queryByText('Alpha')).toBeNull();
    expect(screen.queryByText('Beta')).toBeNull();
    expect(document.querySelectorAll('.mds-row').length).toBe(1);
    expect(document.querySelector('.workflow-row__chevron--collapsed')).toBeTruthy();
  });

  it('opens ticket detail when a node row is activated', async () => {
    const { store, bus } = makeStore();
    stubWorkflowTree(bus);
    bus.stubQuery('ticket.get', {
      ok: false,
      error: 'not needed for open id assertion',
    });
    renderWithStore(<WorkflowsPanel />, { store, bus });

    await waitFor(() => expect(screen.getByText('Alpha')).toBeTruthy());
    fireEvent.click(screen.getByText('Alpha'));
    expect(store.getState().ticketDetail.ticketId).toBe('T-a');
  });

  it('shows the empty hint when both slices are ready with no rows', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('workflow.runs.list', { runs: [] });
    bus.stubQuery('schedule.get', {
      active_tickets: [],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [],
      invalidation_key: 'k',
    });
    renderWithStore(<WorkflowsPanel />, { store, bus });
    await waitFor(() => expect(screen.getByText('No workflows.')).toBeTruthy());
  });

  it('invokes onNewWorkflow from the header plus action', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('workflow.runs.list', { runs: [] });
    bus.stubQuery('schedule.get', {
      active_tickets: [],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [],
      invalidation_key: 'k',
    });
    const onNewWorkflow = vi.fn();
    renderWithStore(<WorkflowsPanel onNewWorkflow={onNewWorkflow} />, { store, bus });
    await waitFor(() => expect(screen.getByText('No workflows.')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'New workflow' }));
    expect(onNewWorkflow).toHaveBeenCalledOnce();
  });
});
