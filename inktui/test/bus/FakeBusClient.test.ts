import type { BusEventListener } from '../../src/bus/BusClient.js';
import { FakeBusClient, matchesFilter, type RecordedRpcCall } from '../../src/bus/FakeBusClient.js';
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

describe('FakeBusClient — events', () => {
  it('delivers an emitted event to a subscriber synchronously', () => {
    const fake = new FakeBusClient();
    const received: BusEvent[] = [];
    fake.subscribe((event) => received.push(event));

    const event = snapshot();
    fake.emit(event);

    expect(received).toEqual([event]);
  });

  it('fans an event out to every live subscriber', () => {
    const fake = new FakeBusClient();
    const a: BusEvent[] = [];
    const b: BusEvent[] = [];
    fake.subscribe((event) => a.push(event));
    fake.subscribe((event) => b.push(event));

    fake.emit(snapshot());

    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);
  });

  it('stops delivering after unsubscribe (subscription lifecycle)', () => {
    const fake = new FakeBusClient();
    const received: BusEvent[] = [];
    const unsubscribe = fake.subscribe((event) => received.push(event));

    fake.emit(snapshot({ key: 'T-1' }));
    expect(fake.subscriberCount).toBe(1);

    unsubscribe();
    expect(fake.subscriberCount).toBe(0);

    fake.emit(snapshot({ key: 'T-2' }));
    expect(received).toHaveLength(1);
  });

  it('unsubscribe is idempotent', () => {
    const fake = new FakeBusClient();
    const unsubscribe = fake.subscribe(() => {});
    unsubscribe();
    expect(() => unsubscribe()).not.toThrow();
    expect(fake.subscriberCount).toBe(0);
  });

  it('a listener that unsubscribes mid-dispatch does not perturb the current fanout', () => {
    const fake = new FakeBusClient();
    const order: string[] = [];
    let unsubscribeSecond: () => void = () => {};
    fake.subscribe(() => {
      order.push('first');
      unsubscribeSecond();
    });
    unsubscribeSecond = fake.subscribe(() => order.push('second'));

    fake.emit(snapshot());

    // The second listener was unsubscribed by the first during dispatch, but the fanout was
    // snapshotted, so it still fires for this event...
    expect(order).toEqual(['first', 'second']);
    // ...and not for the next one.
    order.length = 0;
    fake.emit(snapshot());
    expect(order).toEqual(['first']);
  });

  it('applies a subscription filter (server-side semantics)', () => {
    const fake = new FakeBusClient();
    const tickets: BusEvent[] = [];
    fake.subscribe((event) => tickets.push(event), { entity: 'ticket' });

    fake.emit(snapshot({ entity: 'ticket', key: 'T-1' }));
    fake.emit(snapshot({ entity: 'plan', key: 'P-9' }));

    expect(tickets).toHaveLength(1);
    expect((tickets[0] as StateSnapshotEvent).entity).toBe('ticket');
  });
});

describe('FakeBusClient — rpc', () => {
  it('resolves with a canned fixed reply', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('agent.message', { delivered: true });

    const result = await fake.rpc('agent.message', { agent_id: 'a1', message: 'hi' });

    expect(result).toEqual({ delivered: true });
  });

  it('resolves with a reply computed from the params', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('ticket.quick_kick', (params) => ({ kicked: params.ticket_id }));

    const result = await fake.rpc('ticket.quick_kick', { ticket_id: 'T-42' });

    expect(result).toEqual({ kicked: 'T-42' });
  });

  it('records every rpc call in order for assertions', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('agent.message', {});
    fake.stubRpc('ticket.quick_kick', {});

    await fake.rpc('agent.message', { agent_id: 'a1', message: 'one' });
    await fake.rpc('ticket.quick_kick', { ticket_id: 'T-1' });

    const expected: RecordedRpcCall[] = [
      { method: 'agent.message', params: { agent_id: 'a1', message: 'one' } },
      { method: 'ticket.quick_kick', params: { ticket_id: 'T-1' } },
    ];
    expect(fake.rpcCalls).toEqual(expected);
  });

  it('rpcCalls returns a copy that cannot mutate the internal log', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('agent.message', {});
    await fake.rpc('agent.message', { agent_id: 'a1', message: 'one' });

    const calls = fake.rpcCalls as RecordedRpcCall[];
    calls.pop();

    expect(fake.rpcCalls).toHaveLength(1);
  });

  it('rejects when no stub is registered for the method', async () => {
    const fake = new FakeBusClient();
    await expect(fake.rpc('agent.message', { agent_id: 'a1', message: 'hi' })).rejects.toThrow(
      "no rpc stub for method 'agent.message'",
    );
  });

  it('surfaces a synchronous throw in a handler as a rejection (error path)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('agent.message', () => {
      throw new Error('service unavailable');
    });
    await expect(fake.rpc('agent.message', { agent_id: 'a1', message: 'hi' })).rejects.toThrow(
      'service unavailable',
    );
  });

  it('re-stubbing a method replaces the prior stub', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('agent.message', { v: 1 });
    fake.stubRpc('agent.message', { v: 2 });

    await expect(fake.rpc('agent.message', { agent_id: 'a1', message: 'hi' })).resolves.toEqual({
      v: 2,
    });
  });

  it('still records a call even when the method is unstubbed', async () => {
    const fake = new FakeBusClient();
    await fake.rpc('agent.message', { agent_id: 'a1', message: 'hi' }).catch(() => {});
    expect(fake.rpcCalls).toEqual([
      { method: 'agent.message', params: { agent_id: 'a1', message: 'hi' } },
    ]);
  });
});

describe('matchesFilter', () => {
  const event = snapshot({ entity: 'ticket', ticket_id: 'T-1' });

  it('matches when the filter is absent', () => {
    expect(matchesFilter(event, undefined)).toBe(true);
  });

  it('matches when every present field equals the event field', () => {
    expect(matchesFilter(event, { type: 'state.snapshot', entity: 'ticket' })).toBe(true);
  });

  it('rejects when a present field differs', () => {
    expect(matchesFilter(event, { entity: 'plan' })).toBe(false);
  });

  it('rejects when filtering on a field the event kind lacks', () => {
    // `role` is absent on this snapshot, so a role filter cannot match.
    expect(matchesFilter(event, { role: 'crow' })).toBe(false);
  });

  it('composes fields with AND', () => {
    expect(matchesFilter(event, { entity: 'ticket', ticket_id: 'T-2' })).toBe(false);
    expect(matchesFilter(event, { entity: 'ticket', ticket_id: 'T-1' })).toBe(true);
  });
});

describe('FakeBusClient — interface conformance', () => {
  it('satisfies the BusClient seam (assignable, listener-typed)', () => {
    const fake = new FakeBusClient();
    // The store injects a BusClient; this is the assignment that wiring performs.
    const listener: BusEventListener = () => {};
    const unsubscribe = fake.subscribe(listener);
    expect(typeof unsubscribe).toBe('function');
    unsubscribe();
  });
});
