import type { BusEventListener } from '../../src/bus/BusClient.js';
import {
  FakeBusClient,
  type RecordedCommandCall,
  type RecordedQueryCall,
} from '../../src/bus/FakeBusClient.js';
import { isBusEvent } from '../../src/bus/matchesFilter.js';
import type { BusEvent, StateSnapshotEvent } from '../../src/bus/protocol.js';

// The test idiom every store/selector chunk copies: construct a FakeBusClient, script its events
// and rpc replies, drive the unit under test, assert on what was delivered/called.

/** A minimal `state.snapshot` event — the kind the store subscribes to for slice invalidation. */
function snapshot(overrides: Partial<StateSnapshotEvent> = {}): StateSnapshotEvent {
  return {
    type: 'state.snapshot',
    id: 'evt-1',
    ts: '2026-06-08T00:00:00Z',
    run_id: 'run-1',
    agent_id: '',
    entity: 'ticket',
    key: 'T-1',
    entity_version: 1,
    ...overrides,
  };
}

describe('FakeBusClient — projection events', () => {
  it('delivers an emitted event to a hydrated listener synchronously', async () => {
    const fake = new FakeBusClient();
    const received: BusEvent[] = [];
    await fake.hydrate('roster', (event) => received.push(event));

    const event = snapshot();
    fake.emit(event);

    expect(received).toEqual([event]);
  });

  it('fans an event out to every live hydration', async () => {
    const fake = new FakeBusClient();
    const a: BusEvent[] = [];
    const b: BusEvent[] = [];
    await fake.hydrate('roster', (event) => a.push(event));
    await fake.hydrate('schedule', (event) => b.push(event));

    fake.emit(snapshot());

    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);
  });

  it('stops delivering after unsubscribe (subscription lifecycle)', async () => {
    const fake = new FakeBusClient();
    const received: BusEvent[] = [];
    const hydration = await fake.hydrate('roster', (event) => received.push(event));

    fake.emit(snapshot({ key: 'T-1' }));
    expect(fake.subscriberCount).toBe(1);

    hydration.unsubscribe();
    expect(fake.subscriberCount).toBe(0);

    fake.emit(snapshot({ key: 'T-2' }));
    expect(received).toHaveLength(1);
  });

  it('unsubscribe is idempotent', async () => {
    const fake = new FakeBusClient();
    const hydration = await fake.hydrate('roster', () => {});
    hydration.unsubscribe();
    expect(() => hydration.unsubscribe()).not.toThrow();
    expect(fake.subscriberCount).toBe(0);
  });

  it('a listener that unsubscribes mid-dispatch does not perturb the current fanout', async () => {
    const fake = new FakeBusClient();
    const order: string[] = [];
    let unsubscribeSecond: () => void = () => {};
    await fake.hydrate('roster', () => {
      order.push('first');
      unsubscribeSecond();
    });
    const second = await fake.hydrate('schedule', () => order.push('second'));
    unsubscribeSecond = second.unsubscribe;

    fake.emit(snapshot());

    // The second listener was unsubscribed by the first during dispatch, but the fanout was
    // snapshotted, so it still fires for this event...
    expect(order).toEqual(['first', 'second']);
    // ...and not for the next one.
    order.length = 0;
    fake.emit(snapshot());
    expect(order).toEqual(['first']);
  });
});

describe('FakeBusClient — generated capabilities', () => {
  it('resolves a canned query reply', async () => {
    const fake = new FakeBusClient();
    fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });

    const result = await fake.query('roster.get', {});

    expect(result).toEqual({ invalidation_key: 'iv', sessions: [] });
  });

  it('resolves a command reply computed from the params', async () => {
    const fake = new FakeBusClient();
    fake.stubCommand('plan.create', (params) => ({
      handled: true,
      ok: true,
      plan_name: params.message ?? '',
    }));

    const result = await fake.command('plan.create', {
      message: 'planned',
      body: '',
      auto_name: true,
    });

    expect(result).toMatchObject({ plan_name: 'planned' });
  });

  it('records query and command calls in separate ordered logs', async () => {
    const fake = new FakeBusClient();
    fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
    fake.stubCommand('orchestration.execute', { ok: true, command_id: 'cmd-1' });

    await fake.query('roster.get', {});
    await fake.command('orchestration.execute', {
      kind: 'agent.message',
      payload: { agent_id: 'a1', message: 'one' },
    });

    const queries: RecordedQueryCall[] = [{ name: 'roster.get', params: {} }];
    const commands: RecordedCommandCall[] = [
      {
        name: 'orchestration.execute',
        params: { kind: 'agent.message', payload: { agent_id: 'a1', message: 'one' } },
      },
    ];
    expect(fake.queryCalls).toEqual(queries);
    expect(fake.commandCalls).toEqual(commands);
  });

  it('call getters return copies that cannot mutate the internal logs', async () => {
    const fake = new FakeBusClient();
    fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
    await fake.query('roster.get', {});

    const calls = fake.queryCalls as RecordedQueryCall[];
    calls.pop();

    expect(fake.queryCalls).toHaveLength(1);
  });

  it('rejects when no query stub is registered for the capability', async () => {
    const fake = new FakeBusClient();
    await expect(fake.query('roster.get', {})).rejects.toThrow("no query stub for 'roster.get'");
  });

  it('surfaces a synchronous throw in a handler as a rejection (error path)', async () => {
    const fake = new FakeBusClient();
    fake.stubQuery('roster.get', () => {
      throw new Error('service unavailable');
    });
    await expect(fake.query('roster.get', {})).rejects.toThrow('service unavailable');
  });

  it('re-stubbing a capability replaces the prior stub', async () => {
    const fake = new FakeBusClient();
    fake.stubQuery('roster.get', { invalidation_key: 'one', sessions: [] });
    fake.stubQuery('roster.get', { invalidation_key: 'two', sessions: [] });

    await expect(fake.query('roster.get', {})).resolves.toEqual({
      invalidation_key: 'two',
      sessions: [],
    });
  });

  it('still records a query call when the capability is unstubbed', async () => {
    const fake = new FakeBusClient();
    await fake.query('roster.get', {}).catch(() => {});
    expect(fake.queryCalls).toEqual([{ name: 'roster.get', params: {} }]);
  });
});

describe('FakeBusClient — hydrate', () => {
  it('resolves a stubbed hydrate reply and records the automatic cursor', async () => {
    const fake = new FakeBusClient();
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
    const fake = new FakeBusClient();
    fake.emit(snapshot({ key: 'older' }), 7);
    fake.emit(snapshot({ key: 'newer' }), 12);
    fake.emit(snapshot({ key: 'late-low-seq' }), 9);

    await fake.hydrate('roster');

    expect(fake.hydrateCalls).toEqual([{ topics: ['roster'], cursor: 12 }]);
  });

  it('delivers hydrate tail events until the hydrate result is unsubscribed', async () => {
    const fake = new FakeBusClient();
    const received: BusEvent[] = [];

    const hydration = await fake.hydrate('roster', (event) => received.push(event));
    fake.emit(snapshot({ key: 'T-1' }), 1);
    hydration.unsubscribe();
    fake.emit(snapshot({ key: 'T-2' }), 2);

    expect(received.map((event) => (event as StateSnapshotEvent).key)).toEqual(['T-1']);
  });
});

describe('isBusEvent', () => {
  it('accepts compatibility event payloads and rejects opaque application payloads', () => {
    expect(isBusEvent(snapshot())).toBe(true);
    expect(isBusEvent({ cursor: 1 })).toBe(false);
    expect(isBusEvent(null)).toBe(false);
  });
});

describe('FakeBusClient — interface conformance', () => {
  it('satisfies the BusClient seam (assignable, listener-typed)', () => {
    const fake = new FakeBusClient();
    // The store injects a BusClient; this is the assignment that wiring performs.
    const listener: BusEventListener = () => {};
    const hydration = fake.hydrate('roster', listener);
    expect(hydration).toBeInstanceOf(Promise);
  });
});
