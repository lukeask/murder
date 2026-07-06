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
  HydrateReply,
  HydrateResult,
  HydrateTopics,
  RpcMethod,
  RpcParams,
  RpcPayload,
  RpcResult,
  SubscribeOptions,
  Unsubscribe,
} from './BusClient.js';
import { matchesFilter } from './matchesFilter.js';
import type { BusEvent, EventFilter, HydrateTopic } from './protocol.js';
import { unwrapReadReply } from './readEnvelope.js';

// Re-exported so the bus seam exposes one `matchesFilter` symbol; tests historically import it from
// here. The implementation is the shared single source of truth in `./matchesFilter.js`.
export { matchesFilter };

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

interface Hydration {
  listener: BusEventListener | undefined;
  pending: boolean;
  tailBuffer: BusEvent[];
}

export interface RecordedHydrateCall {
  topics: readonly HydrateTopic[];
  cursor: number | null;
}

export type HydrateHandler = (
  topics: readonly HydrateTopic[],
  cursor: number | null,
) => HydrateReply | Promise<HydrateReply>;

/** The internal, type-erased handler stored per method. A heterogeneous method map can't preserve
 * each key's params/result types, so the map erases to `RpcPayload -> unknown`; the *public*
 * {@link FakeBusClient.stubRpc} / {@link FakeBusClient.rpc} signatures stay fully typed, and the
 * single erasure point is the `stubRpc` cast below. */
type ErasedRpcHandler = (params: RpcPayload) => unknown;

export class FakeBusClient implements BusClient {
  private readonly subscriptions = new Set<Subscription>();
  private readonly hydrations = new Set<Hydration>();
  private readonly rpcHandlers = new Map<RpcMethod, ErasedRpcHandler>();
  private readonly recordedCalls: RecordedRpcCall[] = [];
  private readonly recordedHydrateCalls: RecordedHydrateCall[] = [];
  private hydrateHandler: HydrateHandler | undefined;
  private cursor: number | null = null;

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

  /** Every hydrate made so far, in order. The cursor is the fake's automatically tracked cursor. */
  get hydrateCalls(): readonly RecordedHydrateCall[] {
    return [...this.recordedHydrateCalls];
  }

  /** Number of live subscriptions — lets a test assert subscribe/unsubscribe lifecycle. */
  get subscriberCount(): number {
    return this.subscriptions.size;
  }

  /** Register the fake hydrate reply. Re-stubbing replaces the prior handler. */
  stubHydrate(reply: HydrateReply | HydrateHandler): void {
    this.hydrateHandler =
      typeof reply === 'function' ? (reply as HydrateHandler) : async () => reply;
  }

  /**
   * Deliver `event` to every live subscriber whose filter matches, synchronously and in
   * subscription order. The core driver for store tests: emit a `state.snapshot` and assert the
   * slice re-pulled.
   */
  emit(event: BusEvent, seq?: number | null): void {
    this.observeCursor(seq);
    // Snapshot first so a listener that unsubscribes (or subscribes) during dispatch doesn't
    // perturb this fanout.
    for (const subscription of [...this.subscriptions]) {
      if (matchesFilter(event, subscription.filter)) {
        subscription.listener(event);
      }
    }
    for (const hydration of [...this.hydrations]) {
      if (hydration.pending && event.type !== 'error') {
        hydration.tailBuffer.push(event);
      } else {
        hydration.listener?.(event);
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
    return Promise.resolve().then(() => {
      const stubbed = handler(params);
      // Model the live transport faithfully so the fake exercises the SAME read-RPC envelope
      // contract the real server emits. A `state.*` read handler on the wire returns
      // `{ ok: true, value: <dto> }`; a stub here returns the bare DTO. Wrap it into that envelope,
      // then run the SAME shared unwrap {@link UdsBusClient} runs, so callers receive the unwrapped
      // DTO exactly as they do live — and the wrap/unwrap round-trip (incl. `null` not-found) is now
      // genuinely covered by every fake-backed test rather than silently bypassed.
      const reply = method.startsWith('state.') ? { ok: true, value: stubbed } : stubbed;
      return unwrapReadReply(method, reply as Record<string, unknown>) as RpcResult<M>;
    });
  }

  subscribe(
    listener: BusEventListener,
    filter?: EventFilter,
    _options?: SubscribeOptions,
  ): Unsubscribe {
    const subscription: Subscription = { listener, filter };
    this.subscriptions.add(subscription);
    return () => {
      this.subscriptions.delete(subscription);
    };
  }

  hydrate(topics: HydrateTopics, listener?: BusEventListener): Promise<HydrateResult> {
    const normalizedTopics = normalizeHydrateTopics(topics);
    const callCursor = this.cursor;
    this.recordedHydrateCalls.push({ topics: normalizedTopics, cursor: callCursor });
    const hydration: Hydration = { listener, pending: true, tailBuffer: [] };
    this.hydrations.add(hydration);

    const reply =
      this.hydrateHandler === undefined
        ? Promise.resolve({ snapshots: {}, cursor: callCursor })
        : Promise.resolve().then(() => this.hydrateHandler?.(normalizedTopics, callCursor));

    return reply.then((value) => {
      const resolved: HydrateReply = value ?? { snapshots: {}, cursor: callCursor };
      this.observeCursor(resolved.cursor);
      for (const item of resolved.replay ?? []) {
        hydration.listener?.(item.event);
      }
      for (const event of hydration.tailBuffer) {
        hydration.listener?.(event);
      }
      hydration.tailBuffer = [];
      hydration.pending = false;
      return {
        ...resolved,
        unsubscribe: () => {
          this.hydrations.delete(hydration);
        },
      };
    });
  }

  private observeCursor(cursor: number | null | undefined): void {
    if (typeof cursor !== 'number') {
      return;
    }
    this.cursor = this.cursor === null ? cursor : Math.max(this.cursor, cursor);
  }
}

function normalizeHydrateTopics(topics: HydrateTopics): readonly HydrateTopic[] {
  return typeof topics === 'string' ? [topics] : [...topics];
}
