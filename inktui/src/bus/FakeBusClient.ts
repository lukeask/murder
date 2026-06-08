/**
 * In-memory {@link BusClient} test double — the backbone every store/selector test drives.
 *
 * No socket, no JSON framing, no async transport: a {@link FakeBusClient} lets a test script the
 * two halves of the bus contract directly.
 *
 *  - **Pushed events.** {@link FakeBusClient.emit} delivers an event to every live subscriber
 *    whose filter matches — synchronously, so a test can `emit(snapshot)` and then assert the
 *    store re-pulled the named slice on the very next line, no `await`/flush dance.
 *  - **Canned RPC replies.** {@link FakeBusClient.stubRpc} registers a per-method reply — a fixed
 *    value or a handler computed from the params (and a handler may throw/reject to exercise the
 *    store's error path). {@link FakeBusClient.rpcCalls} records every call for assertions.
 *
 * It implements the real interface exactly, so the same store wiring runs against the fake in
 * tests and the `UdsBusClient` (C2) in production — proving the seam (rule 4).
 */

import type {
  BusClient,
  BusEventListener,
  RpcMethod,
  RpcParams,
  RpcPayload,
  RpcResult,
  Unsubscribe,
} from './BusClient.js';
import type { BusEvent, EventFilter } from './protocol.js';

/** A recorded RPC call, in invocation order. */
export interface RecordedRpcCall {
  method: RpcMethod;
  params: RpcParams<RpcMethod>;
}

/** Computes a reply (or rejection) for an RPC from its params. Async so a stub can model latency
 * or reject to exercise the store's error handling. */
export type RpcHandler<M extends RpcMethod> = (
  params: RpcParams<M>,
) => RpcResult<M> | Promise<RpcResult<M>>;

interface Subscription {
  listener: BusEventListener;
  filter: EventFilter | undefined;
}

/** The internal, type-erased handler stored per method. A heterogeneous method map can't preserve
 * each key's params/result types, so the map erases to `RpcPayload -> unknown`; the *public*
 * {@link FakeBusClient.stubRpc} / {@link FakeBusClient.rpc} signatures stay fully typed, and the
 * single erasure point is the `stubRpc` cast below. */
type ErasedRpcHandler = (params: RpcPayload) => unknown;

export class FakeBusClient implements BusClient {
  private readonly subscriptions = new Set<Subscription>();
  private readonly rpcHandlers = new Map<RpcMethod, ErasedRpcHandler>();
  private readonly recordedCalls: RecordedRpcCall[] = [];

  /**
   * Register a canned reply for `method`. Pass a value for a fixed reply or a handler to compute
   * one from the params (or to throw/reject). Re-stubbing the same method replaces the prior stub.
   */
  stubRpc<M extends RpcMethod>(method: M, reply: RpcResult<M> | RpcHandler<M>): void {
    const handler: RpcHandler<M> =
      typeof reply === 'function' ? (reply as RpcHandler<M>) : () => reply;
    // Erase to the internal handler type; `rpc` only ever invokes it with this method's params.
    this.rpcHandlers.set(method, handler as ErasedRpcHandler);
  }

  /** Every RPC made so far, in order — for `expect(fake.rpcCalls).toEqual(...)` style assertions.
   * Returns a copy so tests can't mutate the internal log. */
  get rpcCalls(): readonly RecordedRpcCall[] {
    return [...this.recordedCalls];
  }

  /** Number of live subscriptions — lets a test assert subscribe/unsubscribe lifecycle. */
  get subscriberCount(): number {
    return this.subscriptions.size;
  }

  /**
   * Deliver `event` to every live subscriber whose filter matches, synchronously and in
   * subscription order. The core driver for store tests: emit a `state.snapshot` and assert the
   * slice re-pulled.
   */
  emit(event: BusEvent): void {
    // Snapshot first so a listener that unsubscribes (or subscribes) during dispatch doesn't
    // perturb this fanout.
    for (const subscription of [...this.subscriptions]) {
      if (matchesFilter(event, subscription.filter)) {
        subscription.listener(event);
      }
    }
  }

  rpc<M extends RpcMethod>(method: M, params: RpcParams<M>): Promise<RpcResult<M>> {
    this.recordedCalls.push({ method, params });
    const handler = this.rpcHandlers.get(method);
    if (handler === undefined) {
      return Promise.reject(new Error(`FakeBusClient: no rpc stub for method '${method}'`));
    }
    // Route through Promise.resolve so a synchronous throw in the handler surfaces as a rejection,
    // matching the real client's always-async contract.
    return Promise.resolve().then(() => handler(params) as RpcResult<M> | Promise<RpcResult<M>>);
  }

  subscribe(listener: BusEventListener, filter?: EventFilter): Unsubscribe {
    const subscription: Subscription = { listener, filter };
    this.subscriptions.add(subscription);
    return () => {
      this.subscriptions.delete(subscription);
    };
  }
}

/**
 * Replicates the server-side {@link EventFilter} semantics (Python `EventFilter.matches`): fields
 * compose with AND; an absent filter field matches any. Exported so a test can reason about which
 * events a filtered subscription should receive.
 */
export function matchesFilter(event: BusEvent, filter: EventFilter | undefined): boolean {
  if (filter === undefined) {
    return true;
  }
  return (
    fieldMatches(filter.role, getField(event, 'role')) &&
    fieldMatches(filter.ticket_id, getField(event, 'ticket_id')) &&
    fieldMatches(filter.type, event.type) &&
    fieldMatches(filter.entity, getField(event, 'entity')) &&
    fieldMatches(filter.target_worker, getField(event, 'target_worker')) &&
    fieldMatches(filter.kind, getField(event, 'kind'))
  );
}

/** A filter field matches when it is absent, or equal to the event's value for that field. */
function fieldMatches<T>(expected: T | undefined, actual: unknown): boolean {
  return expected === undefined || expected === actual;
}

/** Reads an optional field present on only some event kinds, without `any`. Returns `undefined`
 * when the event kind lacks the field (so a filter on it can't match a partial). */
function getField(event: BusEvent, field: string): unknown {
  return (event as unknown as Record<string, unknown>)[field];
}
