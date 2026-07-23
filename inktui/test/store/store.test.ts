import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { createAppStore } from '../../src/store/store.js';

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
    dispose();
  });

  it('refreshes schedule-owned tickets and usage for a projection invalidation', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('schedule.get', {
      invalidation_key: 'schedule-1',
      active_tickets: [],
      recent_done_tickets: [],
      archived_tickets: [],
      usage_gauges: [],
    });
    const { dispose } = createAppStore(fake);
    await flush();

    fake.emitInvalidation({
      type: 'projection.invalidate',
      projection: 'schedule',
      subject_key: 'schedule',
      generation: 1,
      source_fact_id: null,
    });
    await flush();

    expect(fake.queryCalls.map((call) => call.name)).toEqual(['schedule.get', 'schedule.get']);
    dispose();
  });
});
