import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { createAppStore } from '../../src/store/store.js';

describe('workflow runs actions', () => {
  it('stores and refreshes workflow runtime truth independently', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('workflow.runs.get', {
      ok: true,
      run: {
        workflow_id: 'run-1',
        definition_name: 'release',
        definition_version: 1,
        status: 'running',
        revision: 2,
        state: {
          schema_name: 'static_dag',
          schema_version: 1,
          value: { stages: {} },
        },
        created_at: '2026-07-27T00:00:00Z',
        updated_at: '2026-07-27T00:01:00Z',
        started_by: { kind: 'user', id: 'test' },
        correlation: { correlation_id: 'test' },
        definition_snapshot: { name: 'release', stages: [] },
        stage_map: {},
      },
      waits: [],
      error: null,
    });
    const { store, dispose } = createAppStore(fake);

    await store.getState().actions.workflowRuns.setActive('run-1');

    expect(store.getState().workflowRuns).toMatchObject({
      activeWorkflowId: 'run-1',
      status: 'ready',
      error: null,
      activeRun: {
        workflow_id: 'run-1',
        definition_snapshot: { name: 'release', stages: [] },
      },
    });
    dispose();
  });

  it('ignores invalidations when no run is active', async () => {
    const fake = new FakeApplicationClient();
    const { store, dispose } = createAppStore(fake);

    await store.getState().actions.workflowRuns.refresh();

    expect(fake.queryCalls).toEqual([]);
    expect(store.getState().workflowRuns.status).toBe('idle');
    dispose();
  });
});
