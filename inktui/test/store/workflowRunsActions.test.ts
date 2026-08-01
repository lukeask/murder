import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { createAppStore } from '@murder/ui-core/store/store.js';

const sampleRun = {
  workflow_id: 'run-1',
  definition_name: 'release',
  definition_version: 1,
  status: 'running' as const,
  revision: 2,
  state: {
    schema_name: 'static_dag',
    schema_version: 1,
    value: { stages: {} },
  },
  created_at: '2026-07-27T00:00:00Z',
  updated_at: '2026-07-27T00:01:00Z',
  started_by: { kind: 'user' as const, id: 'test' },
  correlation: { correlation_id: 'test' },
  definition_snapshot: { name: 'release', stages: [{ id: 'build', title: 'Build' }] },
  stage_map: { build: 'T-1' },
  parent_ticket_id: 'T-run',
};

describe('workflow runs actions', () => {
  it('lists workflow runs for the Workflows panel', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('workflow.runs.list', { runs: [sampleRun] });
    const { store, dispose } = createAppStore(fake);

    await store.getState().actions.workflowRuns.refreshList();

    expect(store.getState().workflowRuns).toMatchObject({
      listStatus: 'ready',
      listError: null,
      runs: [{ workflow_id: 'run-1', definition_name: 'release' }],
    });
    dispose();
  });

  it('stores and refreshes workflow runtime truth independently', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('workflow.runs.get', {
      ok: true,
      run: sampleRun,
      waits: [],
      error: null,
    });
    fake.stubQuery('workflow.runs.list', { runs: [sampleRun] });
    const { store, dispose } = createAppStore(fake);

    await store.getState().actions.workflowRuns.setActive('run-1');

    expect(store.getState().workflowRuns).toMatchObject({
      activeWorkflowId: 'run-1',
      status: 'ready',
      error: null,
      activeRun: {
        workflow_id: 'run-1',
        definition_snapshot: { name: 'release', stages: [{ id: 'build', title: 'Build' }] },
      },
    });
    dispose();
  });

  it('refresh updates the list even when no run is active', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('workflow.runs.list', { runs: [sampleRun] });
    const { store, dispose } = createAppStore(fake);

    await store.getState().actions.workflowRuns.refresh();

    expect(fake.queryCalls.map((c) => c.name)).toContain('workflow.runs.list');
    expect(store.getState().workflowRuns.listStatus).toBe('ready');
    expect(store.getState().workflowRuns.status).toBe('idle');
    dispose();
  });

  it('refreshActive is a no-op when no run is pinned', async () => {
    const fake = new FakeApplicationClient();
    const { store, dispose } = createAppStore(fake);

    await store.getState().actions.workflowRuns.refreshActive();

    expect(fake.queryCalls).toEqual([]);
    expect(store.getState().workflowRuns.status).toBe('idle');
    dispose();
  });
});
