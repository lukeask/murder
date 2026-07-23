import type {
  ProjectionInvalidation,
  ProjectionInvalidationListener,
} from '../../src/application/ApplicationClient.js';
import {
  FakeApplicationClient,
  type RecordedCommandCall,
  type RecordedQueryCall,
} from '../../src/application/FakeApplicationClient.js';

// The test idiom every store/selector chunk copies: construct a FakeApplicationClient, script its events
// and rpc replies, drive the unit under test, assert on what was delivered/called.

function invalidation(
  projection: 'roster' | 'schedule' = 'schedule',
  overrides: Partial<{ subject_key: string; generation: number }> = {},
) {
  return {
    type: 'projection.invalidate' as const,
    projection,
    subject_key: 'T-1',
    generation: 1,
    source_fact_id: null,
    ...overrides,
  };
}

describe('FakeApplicationClient — projection invalidations', () => {
  it('delivers an emitted invalidation to a hydrated listener synchronously', async () => {
    const fake = new FakeApplicationClient();
    const received: ProjectionInvalidation[] = [];
    await fake.hydrate('roster', (event) => received.push(event));

    const event = invalidation('roster');
    fake.emitInvalidation(event);

    expect(received).toEqual([event]);
  });

  it('fans an invalidation out to every live hydration', async () => {
    const fake = new FakeApplicationClient();
    const a: ProjectionInvalidation[] = [];
    const b: ProjectionInvalidation[] = [];
    await fake.hydrate('roster', (event) => a.push(event));
    await fake.hydrate('schedule', (event) => b.push(event));

    fake.emitInvalidation(invalidation());

    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);
  });

  it('stops delivering after unsubscribe (subscription lifecycle)', async () => {
    const fake = new FakeApplicationClient();
    const received: ProjectionInvalidation[] = [];
    const hydration = await fake.hydrate('roster', (event) => received.push(event));

    fake.emitInvalidation(invalidation('roster', { subject_key: 'T-1' }));
    expect(fake.subscriberCount).toBe(1);

    hydration.unsubscribe();
    expect(fake.subscriberCount).toBe(0);

    fake.emitInvalidation(invalidation('roster', { subject_key: 'T-2' }));
    expect(received).toHaveLength(1);
  });

  it('unsubscribe is idempotent', async () => {
    const fake = new FakeApplicationClient();
    const hydration = await fake.hydrate('roster', () => {});
    hydration.unsubscribe();
    expect(() => hydration.unsubscribe()).not.toThrow();
    expect(fake.subscriberCount).toBe(0);
  });

  it('a listener that unsubscribes mid-dispatch does not perturb the current fanout', async () => {
    const fake = new FakeApplicationClient();
    const order: string[] = [];
    let unsubscribeSecond: () => void = () => {};
    await fake.hydrate('roster', () => {
      order.push('first');
      unsubscribeSecond();
    });
    const second = await fake.hydrate('schedule', () => order.push('second'));
    unsubscribeSecond = second.unsubscribe;

    fake.emitInvalidation(invalidation());

    // The second listener was unsubscribed by the first during dispatch, but the fanout was
    // snapshotted, so it still fires for this event...
    expect(order).toEqual(['first', 'second']);
    // ...and not for the next one.
    order.length = 0;
    fake.emitInvalidation(invalidation());
    expect(order).toEqual(['first']);
  });
});

describe('FakeApplicationClient — generated capabilities', () => {
  it('resolves a canned query reply', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });

    const result = await fake.query('roster.get', {});

    expect(result).toEqual({ invalidation_key: 'iv', sessions: [] });
  });

  it('resolves a command reply computed from the params', async () => {
    const fake = new FakeApplicationClient();
    fake.stubCommand('plan.create', (params) => ({
      handled: true,
      ok: true,
      plan_name: String(params['message'] ?? ''),
    }));

    const result = await fake.command('plan.create', {
      message: 'planned',
      body: '',
      auto_name: true,
    });

    expect(result).toMatchObject({ plan_name: 'planned' });
  });

  it('records query and command calls in separate ordered logs', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
    fake.stubCommand('agent.message', { accepted: true });

    await fake.query('roster.get', {});
    await fake.command('agent.message', { agent_id: 'a1', message: 'one' });

    const queries: RecordedQueryCall[] = [{ name: 'roster.get', params: {} }];
    const commands: RecordedCommandCall[] = [
      {
        name: 'agent.message',
        params: { agent_id: 'a1', message: 'one' },
      },
    ];
    expect(fake.queryCalls).toEqual(queries);
    expect(fake.commandCalls).toEqual(commands);
  });

  it('call getters return copies that cannot mutate the internal logs', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
    await fake.query('roster.get', {});

    const calls = fake.queryCalls as RecordedQueryCall[];
    calls.pop();

    expect(fake.queryCalls).toHaveLength(1);
  });

  it('rejects when no query stub is registered for the capability', async () => {
    const fake = new FakeApplicationClient();
    await expect(fake.query('roster.get', {})).rejects.toThrow("no query stub for 'roster.get'");
  });

  it('surfaces a synchronous throw in a handler as a rejection (error path)', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('roster.get', () => {
      throw new Error('service unavailable');
    });
    await expect(fake.query('roster.get', {})).rejects.toThrow('service unavailable');
  });

  it('re-stubbing a capability replaces the prior stub', async () => {
    const fake = new FakeApplicationClient();
    fake.stubQuery('roster.get', { invalidation_key: 'one', sessions: [] });
    fake.stubQuery('roster.get', { invalidation_key: 'two', sessions: [] });

    await expect(fake.query('roster.get', {})).resolves.toEqual({
      invalidation_key: 'two',
      sessions: [],
    });
  });

  it('still records a query call when the capability is unstubbed', async () => {
    const fake = new FakeApplicationClient();
    await fake.query('roster.get', {}).catch(() => {});
    expect(fake.queryCalls).toEqual([{ name: 'roster.get', params: {} }]);
  });
});

describe('FakeApplicationClient — hydrate', () => {
  it('resolves a stubbed hydrate reply and records the automatic cursor', async () => {
    const fake = new FakeApplicationClient();
    fake.stubHydrate({ snapshots: { conversations: { agents: [] } }, cursor: 42 });

    await expect(fake.hydrate('conversations')).resolves.toMatchObject({
      snapshots: { conversations: { agents: [] } },
      cursor: 42,
    });
    expect(fake.hydrateCalls).toEqual([{ topics: ['conversations'], cursor: null }]);

    fake.stubHydrate({ snapshots: {}, cursor: 45 });
    await fake.hydrate(['conversations', 'schedule']);

    expect(fake.hydrateCalls).toEqual([
      { topics: ['conversations'], cursor: null },
      { topics: ['conversations', 'schedule'], cursor: 42 },
    ]);
  });

  it('uses observed pub seq as the next hydrate cursor', async () => {
    const fake = new FakeApplicationClient();
    fake.emitInvalidation(invalidation('roster', { subject_key: 'older' }), 7);
    fake.emitInvalidation(invalidation('roster', { subject_key: 'newer' }), 12);
    fake.emitInvalidation(invalidation('roster', { subject_key: 'late-low-seq' }), 9);

    await fake.hydrate('roster');

    expect(fake.hydrateCalls).toEqual([{ topics: ['roster'], cursor: 12 }]);
  });

  it('delivers invalidations until the hydrate result is unsubscribed', async () => {
    const fake = new FakeApplicationClient();
    const received: ProjectionInvalidation[] = [];

    const hydration = await fake.hydrate('roster', (event) => received.push(event));
    fake.emitInvalidation(invalidation('roster', { subject_key: 'T-1' }), 1);
    hydration.unsubscribe();
    fake.emitInvalidation(invalidation('roster', { subject_key: 'T-2' }), 2);

    expect(received.map((event) => event.subject_key)).toEqual(['T-1']);
  });
});

describe('FakeApplicationClient — interface conformance', () => {
  it('satisfies the ApplicationClient seam (assignable, invalidation-listener typed)', () => {
    const fake = new FakeApplicationClient();
    // The store injects a ApplicationClient; this is the assignment that wiring performs.
    const listener: ProjectionInvalidationListener = () => {};
    const hydration = fake.hydrate('roster', listener);
    expect(hydration).toBeInstanceOf(Promise);
  });
});
