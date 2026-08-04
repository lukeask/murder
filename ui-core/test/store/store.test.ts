import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { createAppStore } from '@murder/ui-core/store/store.js';

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('createAppStore — projection hydration', () => {
  it('opens one typed projection subscription and applies its snapshots', async () => {
    const fake = new FakeApplicationClient();
    fake.stubHydrate({
      snapshots: {
        roster: {
          invalidation_key: 'roster-1',
          as_of: '2026-07-23T00:00:00Z',
          sessions: [
            {
              agent_id: 'agent-1',
              role: 'crow',
              ticket_id: 'T-1',
              ticket_title: 'Ticket',
              status: 'running',
              display_name: 'crow-1',
              harness: 'codex',
              last_seen: null,
              started_at: null,
              ticket_status: null,
            },
          ],
        },
        settings: {
          settings: {
            background_transparency: 40,
          },
        },
      },
      cursor: 42,
      mode: 'cold',
    });

    const { store, dispose } = createAppStore(fake);
    await flush();

    expect(fake.hydrateCalls).toEqual([
      {
        topics: [
          'conversations',
          'roster',
          'schedule',
          'favorites',
          'templates',
          'themes',
          'workflows',
          'workflow_runs',
          'settings',
        ],
        cursor: null,
      },
    ]);
    expect(store.getState().hydration).toMatchObject({
      status: 'ready',
      projections: { cursor: 42, mode: 'cold' },
    });
    expect(store.getState().roster.rows[0]?.agentId).toBe('agent-1');
    expect(store.getState().settings.backgroundTransparency).toBe(40);
    dispose();
  });

  it('refreshes the workflow-run list and active run for a workflow_runs invalidation', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('workflow.runs.get', {
      ok: false,
      run: null,
      waits: [],
      error: 'not_found',
    });
    fake.stubQuery('workflow.runs.list', { runs: [] });
    const { store, dispose } = createAppStore(fake);
    await flush();

    await store.getState().actions.workflowRuns.setActive('run-1');
    expect(fake.queryCalls).toHaveLength(1);

    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'workflow_runs',
      subject_key: 'run-1',
      generation: 1,
      source_fact_id: null,
    });
    await flush();

    expect(fake.queryCalls).toEqual([
      {
        name: 'workflow.runs.get',
        params: { workflow_id: 'run-1', include_waits: false },
      },
      {
        name: 'workflow.runs.list',
        params: { limit: 200 },
      },
      {
        name: 'workflow.runs.get',
        params: { workflow_id: 'run-1', include_waits: false },
      },
    ]);

    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'approvals',
      subject_key: 'approval-1',
      generation: 1,
      source_fact_id: null,
    });
    await flush();
    expect(fake.queryCalls).toHaveLength(3);
    dispose();
  });

  it('refreshes schedule-owned tickets and usage for a projection invalidation', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('schedule.get', {
      invalidation_key: 'schedule-1',
      active_tickets: [
        {
          id: 'T-1',
          title: 'One',
          status: 'ready',
          last_update_at: '2026-01-01T00:00:00Z',
          last_update_label: 'up',
          pending_dep_ids: [],
        },
      ],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [
        {
          harness: 'codex',
          window_key: '5h',
          pct: 12,
          t_until_reset_minutes: 30,
        },
      ],
    });
    const { store, dispose } = createAppStore(fake);
    await flush();

    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'schedule',
      subject_key: 'schedule',
      generation: 1,
      source_fact_id: null,
    });
    await flush();

    expect(fake.queryCalls.map((call) => call.name)).toEqual(['schedule.get']);
    expect(store.getState().tickets.rows).toHaveLength(1);
    expect(store.getState().tickets.rows[0]?.id).toBe('T-1');
    expect(store.getState().usage.rows).toHaveLength(1);
    expect(store.getState().usage.rows[0]?.harness).toBe('codex');
    expect(store.getState().tickets.status).toBe('ready');
    expect(store.getState().usage.status).toBe('ready');
    dispose();
  });

  it('coalesces repeated schedule invalidations into one query', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('schedule.get', {
      invalidation_key: 'schedule-1',
      active_tickets: [
        {
          id: 'T-1',
          title: 'One',
          status: 'ready',
          last_update_at: '2026-01-01T00:00:00Z',
          last_update_label: 'up',
          pending_dep_ids: [],
        },
      ],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [],
    });
    const { store, dispose } = createAppStore(fake);
    await flush();

    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'schedule',
      subject_key: 'schedule',
      generation: 1,
      source_fact_id: null,
    });
    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'schedule',
      subject_key: 'schedule',
      generation: 2,
      source_fact_id: null,
    });
    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'schedule',
      subject_key: 'schedule',
      generation: 3,
      source_fact_id: null,
    });
    await flush();
    await flush();

    expect(fake.queryCalls.map((call) => call.name)).toEqual(['schedule.get']);
    expect(store.getState().tickets.rows[0]?.id).toBe('T-1');
    dispose();
  });

  it('hydration and refresh produce equal rows from equal schedule replies', async () => {
    const schedule = {
      invalidation_key: 'schedule-1',
      active_tickets: [
        {
          id: 'T-1',
          title: 'One',
          status: 'ready',
          last_update_at: '2026-01-01T00:00:00Z',
          last_update_label: 'up',
          pending_dep_ids: [],
        },
      ],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [
        {
          harness: 'codex',
          window_key: '5h',
          pct: 12,
          t_until_reset_minutes: 30,
        },
      ],
    };
    const fake = new FakeApplicationClient();
    fake.stubHydrate({
      snapshots: { schedule },
      cursor: 1,
      mode: 'cold',
    });
    fake.stubQuery('schedule.get', schedule);
    const { store, dispose } = createAppStore(fake);
    await flush();

    const hydratedTickets = store.getState().tickets.rows;
    const hydratedUsage = store.getState().usage.rows;

    await store.getState().actions.tickets.refresh();
    expect(store.getState().tickets.rows).toEqual(hydratedTickets);
    expect(store.getState().usage.rows).toEqual(hydratedUsage);
    dispose();
  });

  it('a usage-scoped refresh does not disturb ticket loading state', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('schedule.get', {
      invalidation_key: 'schedule-1',
      active_tickets: [],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [
        {
          harness: 'codex',
          window_key: '5h',
          pct: 1,
          t_until_reset_minutes: 1,
        },
      ],
    });
    const { store, dispose } = createAppStore(fake);
    await flush();

    store.setState({
      tickets: {
        rows: [
          {
            id: 'T-1',
            title: 'One',
            status: 'ready',
            lastUpdateAt: '2026-01-01T00:00:00Z',
            lastUpdateLabel: 'up',
            scheduleAt: null,
            harness: null,
            model: null,
            pendingDepIds: [],
            parent: null,
          },
        ],
        status: 'ready',
        error: null,
      },
    });
    const ticketsBefore = store.getState().tickets;

    let resolveReply: ((value: unknown) => void) | undefined;
    fake.stubQuery(
      'schedule.get',
      () =>
        new Promise((resolve) => {
          resolveReply = resolve;
        }),
    );

    // Same loadingKeys scoping sample()/setSteering use after a successful write.
    const pending = store.getState().actions.usage.refresh({ loadingKeys: ['usage'] });
    await flush();

    expect(store.getState().tickets).toBe(ticketsBefore);
    expect(store.getState().tickets.status).toBe('ready');

    resolveReply?.({
      invalidation_key: 'schedule-2',
      active_tickets: [],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [
        {
          harness: 'codex',
          window_key: '5h',
          pct: 50,
          t_until_reset_minutes: 2,
        },
      ],
    });
    await pending;
    expect(store.getState().usage.rows[0]?.pct).toBe(50);
    expect(store.getState().tickets.status).toBe('ready');
    dispose();
  });

  it('a usage-scoped refresh failure does not mark tickets as error', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('schedule.get', {
      invalidation_key: 'schedule-1',
      active_tickets: [],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [
        {
          harness: 'codex',
          window_key: '5h',
          pct: 1,
          t_until_reset_minutes: 1,
        },
      ],
    });
    const { store, dispose } = createAppStore(fake);
    await flush();

    store.setState({
      tickets: {
        rows: [
          {
            id: 'T-1',
            title: 'One',
            status: 'ready',
            lastUpdateAt: '2026-01-01T00:00:00Z',
            lastUpdateLabel: 'up',
            scheduleAt: null,
            harness: null,
            model: null,
            pendingDepIds: [],
            parent: null,
          },
        ],
        status: 'ready',
        error: null,
      },
      usage: {
        rows: [
          {
            harness: 'codex',
            windowKey: '5h',
            pct: 1,
            tUntilResetMinutes: 1,
            tPeriodMinutes: 0,
            steering: 'auto',
          },
        ],
        status: 'ready',
        error: null,
      },
    });
    const ticketsBefore = store.getState().tickets;

    fake.stubQuery('schedule.get', () => {
      throw new Error('schedule unavailable');
    });

    await expect(
      store.getState().actions.usage.refresh({ loadingKeys: ['usage'] }),
    ).resolves.toBeUndefined();

    expect(store.getState().tickets).toBe(ticketsBefore);
    expect(store.getState().tickets.status).toBe('ready');
    expect(store.getState().tickets.error).toBeNull();
    expect(store.getState().usage.status).toBe('error');
    expect(store.getState().usage.error).toBe('schedule unavailable');
    dispose();
  });
});
