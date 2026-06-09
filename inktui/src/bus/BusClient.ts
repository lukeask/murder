/**
 * The transport-agnostic seam between the store and the bus (README rule 4).
 *
 * `BusClient` is the one interface the store's action layer calls and the only thing the real
 * Unix-socket client (C2, `UdsBusClient`) and the test double ({@link FakeBusClient}) both
 * implement. It is dependency-injected into the store so tests fake it and a future WebSocket
 * bridge swaps transport with zero store edits. Nothing terminal- or socket-specific appears
 * here — only the protocol types from `protocol.ts`.
 *
 * Two operations, mirroring the two directions of the bus contract:
 *
 *  - {@link BusClient.rpc} — view -> service request/response (the only view->bus write path,
 *    rule 3). Typed by {@link RpcMethods}: the method name selects its params and result types, so
 *    a typo or a wrong-shaped payload is a compile error.
 *  - {@link BusClient.subscribe} — service -> view server-push. Key-only events name the slice
 *    that changed; the store re-pulls that slice. Delivered to a **callback**, not an async
 *    iterator — see the rationale on the method.
 */

import type { BusEvent, EventFilter } from './protocol.js';

/**
 * Typed registry of RPC methods: method name -> { params, result }. The bus contract's "Methods
 * (view -> service)" list lands here as it stabilizes (`ticket.quick_kick`, `crow.spawn_rogue`,
 * `agent.message`, `ticket.quick_create`, …). C1 seeds it with the contract's no-new-RPC methods
 * so the type is real, not a placeholder; later chunks add a line per method as the service (B13)
 * exposes it.
 *
 * Each entry's `params` is the RPC body and `result` the reply payload. Both default-extend
 * `RpcPayload` so an under-specified method is still type-safe, never `any`.
 */
export type RpcPayload = Record<string, unknown>;

export interface RpcMethods {
  /** Kick an existing ticket. */
  'ticket.quick_kick': { params: { ticket_id: string }; result: RpcPayload };
  /** Deliver a message to an agent (chat). */
  'agent.message': { params: { agent_id: string; message: string }; result: RpcPayload };
  /** Submit a captured note. */
  'notetaker.capture.submit': { params: RpcPayload; result: RpcPayload };
  /**
   * Submit a command to the orchestrator command bus (live `command.submit`). Returns the assigned
   * `command_id`; callers poll {@link RpcMethods['command.status']} for the terminal result. This is
   * the write seam for orchestrator operations (`ticket.quick_create`, `crow.spawn_rogue`,
   * `agent.message`-as-command) — see {@link submitCommand}.
   */
  'command.submit': { params: CommandSubmitParams; result: CommandSubmitResult };
  /** Poll a submitted command's status (live `command.status`). */
  'command.status': { params: { command_id: string }; result: CommandStatusResult };
}

/** Params for the live `command.submit` RPC (mirrors `murder/app/service/host.py`). */
export interface CommandSubmitParams extends Record<string, unknown> {
  /** The worker that handles this command kind (e.g. `'orchestrator'`). */
  readonly target_worker: string;
  /** The command kind the worker dispatches on (e.g. `'crow.spawn_rogue'`). */
  readonly kind: string;
  /** The command payload — shape depends on `kind`. */
  readonly payload: RpcPayload;
  /** The submitting agent id (defaults server-side to `'rpc-client'`). */
  readonly agent_id?: string;
  readonly correlation_id?: string;
  readonly idempotency_key?: string;
}

/** Reply from `command.submit`: the assigned command id. */
export interface CommandSubmitResult {
  readonly ok: boolean;
  readonly command_id: string;
}

/** Reply from `command.status`: terminal/in-flight state. `result_json` is a JSON-encoded string. */
export interface CommandStatusResult {
  readonly ok: boolean;
  readonly status?: string;
  readonly result_json?: string | null;
  readonly last_error?: string | null;
  readonly command_id?: string;
}

/** A method name known to {@link RpcMethods}. */
export type RpcMethod = keyof RpcMethods;

/** The params type for a given RPC method. */
export type RpcParams<M extends RpcMethod> = RpcMethods[M]['params'];

/** The result type for a given RPC method. */
export type RpcResult<M extends RpcMethod> = RpcMethods[M]['result'];

/**
 * Receives one server-pushed event. Registered via {@link BusClient.subscribe}; called once per
 * matching event for the lifetime of the subscription.
 */
export type BusEventListener = (event: BusEvent) => void;

/**
 * Cancels a subscription. Idempotent: calling it more than once is a no-op. Returned by
 * {@link BusClient.subscribe} so the caller disposes exactly what it created (the Zustand
 * `subscribe` idiom — register, get a disposer, call it on teardown).
 */
export type Unsubscribe = () => void;

export interface BusClient {
  /**
   * Issue an RPC and resolve with its typed result. The sole view->bus write path (rule 3): the
   * store's actions call this; components never do. Rejects if the service returns an error
   * envelope or the call times out — the error policy itself lives in the implementation (C2),
   * not in this interface.
   */
  rpc<M extends RpcMethod>(method: M, params: RpcParams<M>): Promise<RpcResult<M>>;

  /**
   * Subscribe to server-pushed events, optionally narrowed by `filter` (server-applied; an absent
   * field matches any). Returns an {@link Unsubscribe} disposer.
   *
   * Callback, not async iterator — deliberate. Slice invalidation is a fire-and-forget push: an
   * event arrives, the store ref-swaps the named slice, the relevant subscribers re-render. That
   * is the observer shape Zustand and `useSyncExternalStore` are built on, so a callback + disposer
   * drops straight into the store with no pull-loop to own and no per-consumer iterator lifecycle
   * to coordinate when several slices subscribe to the same stream. The real client (C2) owns its
   * socket read loop internally and fans each frame out to the registered listeners; the async
   * iterator stays an implementation detail below this seam, never leaking into the store.
   */
  subscribe(listener: BusEventListener, filter?: EventFilter): Unsubscribe;
}
