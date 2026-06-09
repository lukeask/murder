/**
 * The real {@link BusClient}: a single persistent Unix-socket JSON-RPC connection (README rule 4).
 *
 * This is the one place reconnect/backoff and the bus error policy live. Every store action calls
 * the {@link BusClient} interface; only this module knows there is a socket underneath. The
 * {@link FakeBusClient} swaps in for tests with zero store edits — that is the seam C1 drew and this
 * module proves.
 *
 * ## Framing (mirrors `murder/bus/client.py` / `transport_socket.py`)
 *
 * JSON-lines: one wire envelope per `\n`-terminated line. Outbound, we `JSON.stringify(envelope) +
 * '\n'`. Inbound, a {@link LineBuffer} reassembles partial reads and splits multiple envelopes that
 * arrive in one chunk, then each line is parsed as a {@link WireMessage}. UUIDs/datetimes are plain
 * strings on the wire, exactly as the Python `default=str` dump produces.
 *
 * ## Connection model — *single multiplexed connection* (a deliberate divergence from Python)
 *
 * The Python client opens a short-lived connection per RPC/publish and a dedicated long-lived
 * connection per subscription. That split makes sense for an ephemeral CLI that fires one request
 * and exits; it is the wrong shape for a long-running TUI that holds many concurrent subscriptions
 * and issues a steady stream of RPCs. We collapse it to **one** persistent connection, multiplexed:
 *
 *   - one TCP-of-Unix-sockets handshake (Hello/Ack) on connect;
 *   - every RPC rides that connection and is paired to its reply by `correlation_id`;
 *   - every subscription rides that connection too — each `sub` carries its own `correlation_id`,
 *     and inbound `pub` frames are fanned out to the listeners whose server-side filter matches.
 *
 * The *observable* behavior the contract pins down is identical to Python: a Hello-first handshake,
 * `PROTOCOL_VERSION` refusal on mismatch, `correlation_id` reply pairing, and skipping interleaved
 * `wake` frames on every path. What changes is purely internal: one socket instead of N, which is
 * what lets reconnect/backoff be a single concern rather than scattered per call.
 *
 * ## Reconnect / backoff policy
 *
 * On an unexpected drop the client reconnects automatically with **exponential backoff + full
 * jitter**, capped: delay = random(0, min(cap, base * 2^attempt)). Defaults: base 250ms, cap 10s
 * (see {@link BackoffConfig}). Full jitter (random across the whole window, not base + jitter) is
 * the AWS-architecture-blog recommendation — it spreads reconnect storms when many clients drop at
 * once. The attempt counter resets to 0 after a *successful* handshake, so a brief blip costs one
 * short delay, not a permanently-elevated one. A {@link PROTOCOL_VERSION} mismatch is **not**
 * retried — it is a permanent disagreement, so the client gives up and surfaces it (see error
 * policy). {@link UdsBusClient.close} stops reconnection for good.
 *
 * ## Error policy (stated once, here, so every consumer inherits the same rules)
 *
 *   - **RPC** rejects its promise when: the call exceeds its timeout (`timeout_s + 1.0`, matching
 *     Python's receive deadline), the server returns an `err` envelope, or the connection drops
 *     with the call still outstanding. The store action that issued the RPC is the one place that
 *     decides what a rejection means to the user; this layer never swallows an RPC failure.
 *   - **Subscriptions survive reconnect.** A subscription is a standing intent, not a one-shot. The
 *     client records every live subscription (listener + filter) and **re-sends its `sub` frame
 *     after each successful reconnect**, so the store never re-subscribes and never sees a gap it
 *     has to paper over. Slice invalidation is key-only, so the store simply re-pulls the named
 *     slices on the next event — exactly as it would on first connect.
 *   - **What surfaces vs. what is handled internally:** connection loss and reconnection are
 *     handled here and are invisible to callers *except* through outstanding-RPC rejection (above).
 *     Transient transport errors are logged via the injected logger and retried via backoff; they
 *     do not bubble. The single fatal, non-retried condition is a protocol-version mismatch.
 */

import type { Buffer } from 'node:buffer';
import { randomUUID } from 'node:crypto';
import { connect as netConnect, type Socket } from 'node:net';
import { join } from 'node:path';

import type {
  BusClient,
  BusEventListener,
  RpcMethod,
  RpcParams,
  RpcResult,
  Unsubscribe,
} from './BusClient.js';
import {
  type BusEvent,
  type ClientKind,
  DEFAULT_RPC_TIMEOUT_S,
  type EventFilter,
  type HelloMessage,
  PROTOCOL_VERSION,
  type RpcMessage,
  SOCKET_BASENAME,
  SOCKET_RUNTIME_SUBDIR,
  type SubMessage,
  type WireMessage,
} from './protocol.js';

/** A minimal logger surface so the client can report reconnects/transport errors without coupling
 * to any logging library. Injected (rule 4); a test can pass a no-op, production can pass a real
 * one. Matches the `console` shape so `console` itself is a valid argument. */
export interface BusLogger {
  warn(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
}

/** A no-op logger — the default, so the client is silent unless a logger is injected. */
const SILENT_LOGGER: BusLogger = {
  warn: () => {},
  info: () => {},
};

/** Reconnect backoff parameters. Exponential with full jitter, capped. See the module docstring. */
export interface BackoffConfig {
  /** First-retry ceiling, doubled per attempt before the cap. */
  baseMs: number;
  /** Maximum delay; the exponential is clamped here. */
  capMs: number;
}

const DEFAULT_BACKOFF: BackoffConfig = { baseMs: 250, capMs: 10_000 };

/** Injected timing seam so tests drive reconnect deterministically without real wall-clock waits.
 * Defaults to real `setTimeout` and `Math.random`. */
export interface Clock {
  /** Resolves after `ms`; the returned `cancel` aborts a pending wait (used on `close`). */
  sleep(ms: number): { promise: Promise<void>; cancel: () => void };
  /** A value in [0, 1); abstracted so a test can make jitter deterministic. */
  random(): number;
}

const REAL_CLOCK: Clock = {
  sleep(ms: number) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resolveFn: (() => void) | undefined;
    const promise = new Promise<void>((resolve) => {
      resolveFn = resolve;
      timer = setTimeout(resolve, ms);
    });
    return {
      promise,
      cancel: () => {
        if (timer !== undefined) {
          clearTimeout(timer);
        }
        resolveFn?.();
      },
    };
  },
  random: Math.random,
};

/** Constructor dependencies — all injected so the client stays testable (rule 4). Everything but
 * `socketPath` has a production-sane default. */
export interface UdsBusClientOptions {
  /** Absolute path to the bus socket. Use {@link defaultSocketPath} to derive it from a runtime dir. */
  socketPath: string;
  /** Identifies this client to the supervisor; defaults to `'tui'`. */
  clientKind?: ClientKind;
  /** Stable across reconnects so the supervisor can resume RPC/presence state. Defaults to a
   * `${clientKind}-${uuid}`. */
  clientId?: string;
  /** Per-RPC timeout in seconds; the client waits `rpcTimeoutS + 1.0` for the reply (mirrors
   * Python's receive deadline). Defaults to {@link DEFAULT_RPC_TIMEOUT_S}. Injectable so tests can
   * drive the timeout path without real waits. */
  rpcTimeoutS?: number;
  backoff?: BackoffConfig;
  clock?: Clock;
  logger?: BusLogger;
}

/** Raised when the server's protocol version disagrees with ours. Permanent — not retried. */
export class ProtocolVersionMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolVersionMismatchError';
  }
}

/** Raised when an RPC outlives its deadline. */
export class RpcTimeoutError extends Error {
  constructor(method: string, timeoutS: number) {
    super(`rpc '${method}' timed out after ${timeoutS}s`);
    this.name = 'RpcTimeoutError';
  }
}

/** Raised when the connection drops with an RPC still outstanding, or before the handshake completes. */
export class ConnectionLostError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConnectionLostError';
  }
}

/**
 * Reassembles a `\n`-delimited byte stream into whole lines. The socket delivers arbitrary chunks:
 * a single read may hold half a line, several lines, or a line split across two reads. {@link push}
 * appends a chunk and returns every *complete* line it now holds, retaining any trailing partial
 * for the next chunk. This is the inbound half of JSON-lines framing (Python's `_readline`).
 */
export class LineBuffer {
  private buffer = '';

  /** Append a chunk; return the complete lines (without their trailing `\n`) it completes. */
  push(chunk: string): string[] {
    this.buffer += chunk;
    const lines: string[] = [];
    let newlineIndex = this.buffer.indexOf('\n');
    while (newlineIndex >= 0) {
      lines.push(this.buffer.slice(0, newlineIndex));
      this.buffer = this.buffer.slice(newlineIndex + 1);
      newlineIndex = this.buffer.indexOf('\n');
    }
    return lines;
  }
}

/** A subscription's standing intent — replayed onto every reconnected socket. The `correlationId`
 * is regenerated per reconnect so each `sub` frame is uniquely identified on the new connection. */
interface Subscription {
  readonly listener: BusEventListener;
  readonly filter: EventFilter | undefined;
  correlationId: string;
}

/** An in-flight RPC awaiting its `ack`/`err`/timeout. Keyed by `correlation_id`. */
interface PendingRpc {
  resolve(result: Record<string, unknown>): void;
  reject(error: Error): void;
  /** Clears the timeout timer; called on settle so a resolved RPC can't later "time out". */
  cancelTimeout(): void;
}

/** The lifecycle of the single connection. */
type ConnectionState = 'idle' | 'connecting' | 'connected' | 'closed';

export class UdsBusClient implements BusClient {
  private readonly socketPath: string;
  private readonly clientKind: ClientKind;
  private readonly clientId: string;
  private readonly rpcTimeoutS: number;
  private readonly backoff: BackoffConfig;
  private readonly clock: Clock;
  private readonly logger: BusLogger;

  private state: ConnectionState = 'idle';
  private socket: Socket | undefined;
  private lineBuffer = new LineBuffer();

  private readonly subscriptions = new Set<Subscription>();
  private readonly pendingRpcs = new Map<string, PendingRpc>();

  /** Tracks the active backoff wait so {@link close} can abort it. */
  private pendingSleep: { cancel: () => void } | undefined;
  /** Resolves once the handshake completes, so {@link rpc}/{@link subscribe} can wait for a live
   * connection rather than racing the initial connect. */
  private handshakeReady: Promise<void> | undefined;

  constructor(options: UdsBusClientOptions) {
    this.socketPath = options.socketPath;
    this.clientKind = options.clientKind ?? 'tui';
    this.clientId = options.clientId ?? `${this.clientKind}-${randomUUID()}`;
    this.rpcTimeoutS = options.rpcTimeoutS ?? DEFAULT_RPC_TIMEOUT_S;
    this.backoff = options.backoff ?? DEFAULT_BACKOFF;
    this.clock = options.clock ?? REAL_CLOCK;
    this.logger = options.logger ?? SILENT_LOGGER;
  }

  /**
   * Open the connection and complete the handshake. Idempotent: a second call returns the same
   * in-flight handshake. After this resolves the client is connected and will auto-reconnect on
   * drop until {@link close}. Rejects only on a permanent {@link ProtocolVersionMismatchError}
   * during the *first* handshake; transient failures are retried internally via backoff.
   */
  connect(): Promise<void> {
    if (this.state === 'closed') {
      return Promise.reject(new ConnectionLostError('client is closed'));
    }
    this.handshakeReady ??= this.runConnectLoop();
    return this.handshakeReady;
  }

  /** {@inheritDoc BusClient.rpc} */
  async rpc<M extends RpcMethod>(method: M, params: RpcParams<M>): Promise<RpcResult<M>> {
    const socket = await this.ensureConnected();
    const correlationId = `rpc-${randomUUID()}`;
    const timeoutS = this.rpcTimeoutS;
    // Match Python's receive deadline: the server is given `timeout_s`, the client waits one extra
    // second so a server that returns right at its own deadline still beats the client's timer.
    const recvTimeoutMs = (timeoutS + 1.0) * 1000;

    const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRpcs.delete(correlationId);
        reject(new RpcTimeoutError(method, timeoutS));
      }, recvTimeoutMs);
      this.pendingRpcs.set(correlationId, {
        resolve,
        reject,
        cancelTimeout: () => clearTimeout(timer),
      });
      const message: RpcMessage = {
        op: 'rpc',
        schema_version: PROTOCOL_VERSION,
        correlation_id: correlationId,
        args: { target: method, body: params, timeout_s: timeoutS },
      };
      this.writeMessage(socket, message);
    });
    // Read-RPC envelope unwrap. The service wraps every `state.*` read handler's DTO as
    // `{ ok: true, value: <dto> }` (the `_value()` helper in `murder/app/service/host.py`), a
    // shape the still-live Textual client depends on (`_request_value`/`_request_optional` read
    // `reply["value"]`). The Ink store reads read-RPC fields at TOP LEVEL (`reply.sessions`,
    // `reply.body`, projections in `listSlice.ts`), matching the unwrapped DTO that `FakeBusClient`
    // returns. So unwrap the envelope HERE, at the single real-transport seam, gated on the
    // `state.` prefix: every wrapped handler is `state.*`, and writes/commands (`command.*`,
    // `ticket.*`, `agent.*`, `image.*`) already return `{ ok, ...fields }` top-level and must NOT
    // be unwrapped. Doing it here (not in the store layer) keeps the shared store code and all
    // fake-backed tests untouched. See `.murder/notes/ink-service-integration-gaps.md` §2.
    if (method.startsWith('state.') && isReadEnvelope(result)) {
      // Return `.value` verbatim — including `null`, which `_state_ticket_detail` (and the
      // `*_display` reads) emit for not-found via `_value(None)`. That `null` is the not-found signal
      // the store's detail/doc-view paths key on, and it matches the unwrapped DTO `FakeBusClient`
      // returns; coercing it (e.g. `?? {}`) would resurrect the fake-vs-live divergence this fixes.
      return result.value as RpcResult<M>;
    }
    return result as RpcResult<M>;
  }

  /** {@inheritDoc BusClient.subscribe} */
  subscribe(listener: BusEventListener, filter?: EventFilter): Unsubscribe {
    const subscription: Subscription = {
      listener,
      filter,
      correlationId: `sub-${randomUUID()}`,
    };
    this.subscriptions.add(subscription);
    if (this.state === 'connected' && this.socket !== undefined) {
      // Already live: send the `sub` frame now. (A subscription added *before* the handshake is
      // instead replayed by `openAndHandshake`, so it is sent exactly once on every connection —
      // doing both here would double-send.)
      this.sendSubscription(this.socket, subscription);
    } else {
      // Not yet connected: ensure the connection is being established; the handshake's replay loop
      // will send this subscription's `sub` frame when it completes. Errors are surfaced through
      // the connect()/rpc() paths, not here.
      void this.connect().catch(() => {});
    }
    return () => {
      this.subscriptions.delete(subscription);
      // The server tears the subscription down when the connection closes; with a multiplexed
      // connection we cannot drop a single sub server-side, so unsubscribe is local: the listener
      // simply stops being fanned out to. This is the disposer contract the store relies on.
    };
  }

  /** Stop reconnection, reject every outstanding RPC, and close the socket. Idempotent. */
  close(): void {
    if (this.state === 'closed') {
      return;
    }
    this.state = 'closed';
    this.pendingSleep?.cancel();
    this.failAllPendingRpcs(new ConnectionLostError('client closed'));
    this.subscriptions.clear();
    this.teardownSocket();
  }

  /** Reads the `closed` state opaquely so the connect loop's control flow is not narrowed by TS's
   * flow analysis (the field is mutated across `await` points and from {@link close}). */
  private isClosed(): boolean {
    return this.state === 'closed';
  }

  // === Connection loop ========================================================

  /** Connect, handshake, then read until the socket drops; on an unexpected drop, back off and
   * retry. Resolves its promise once the *first* handshake succeeds; a permanent version mismatch
   * rejects it. */
  private async runConnectLoop(): Promise<void> {
    let attempt = 0;
    let firstHandshakeSettled = false;
    let resolveFirst!: () => void;
    let rejectFirst!: (error: Error) => void;
    const firstHandshake = new Promise<void>((resolve, reject) => {
      resolveFirst = resolve;
      rejectFirst = reject;
    });

    const loop = async (): Promise<void> => {
      while (!this.isClosed()) {
        try {
          await this.openAndHandshake();
          attempt = 0; // a clean handshake resets backoff
          if (!firstHandshakeSettled) {
            firstHandshakeSettled = true;
            resolveFirst();
          }
          await this.readUntilClosed();
        } catch (error) {
          if (error instanceof ProtocolVersionMismatchError) {
            // Permanent disagreement — do not retry. Surface to the first caller and stop.
            this.state = 'closed';
            if (!firstHandshakeSettled) {
              firstHandshakeSettled = true;
              rejectFirst(error);
            }
            this.failAllPendingRpcs(error);
            return;
          }
          this.logger.warn(`bus connection error: ${stringifyError(error)}`);
        }
        if (this.isClosed()) {
          break;
        }
        // Drop with the connection lost: fail outstanding RPCs, then back off and retry.
        this.failAllPendingRpcs(new ConnectionLostError('connection dropped'));
        const delay = this.nextBackoffMs(attempt);
        attempt += 1;
        this.logger.info(`bus reconnecting in ${Math.round(delay)}ms (attempt ${attempt})`);
        const sleeper = this.clock.sleep(delay);
        this.pendingSleep = sleeper;
        await sleeper.promise;
        this.pendingSleep = undefined;
      }
    };

    void loop();
    return firstHandshake;
  }

  /** Open the socket, send Hello, await the matching Ack (skipping wake frames), then mark
   * connected and replay every standing subscription onto the fresh connection. */
  private async openAndHandshake(): Promise<void> {
    this.state = 'connecting';
    this.lineBuffer = new LineBuffer();
    const socket = await this.openSocket();
    this.socket = socket;

    const correlationId = `hello-${randomUUID()}`;
    const hello: HelloMessage = {
      op: 'hello',
      schema_version: PROTOCOL_VERSION,
      correlation_id: correlationId,
      body: {
        protocol_version: PROTOCOL_VERSION,
        client_kind: this.clientKind,
        client_id: this.clientId,
      },
    };

    // The handshake reads through `this.lineBuffer` (reset empty above), so any bytes the ack's
    // chunk carries *past* the ack line — a complete pipelined frame, or a trailing partial —
    // stay in `this.lineBuffer` for the steady-state read loop rather than being dropped with a
    // throwaway local buffer. `handshakeExchange` returns the complete lines that followed the ack.
    const trailingLines = await this.handshakeExchange(socket, hello, correlationId);
    this.state = 'connected';
    // Re-establish every standing subscription so the store never re-subscribes (error policy).
    for (const subscription of this.subscriptions) {
      subscription.correlationId = `sub-${randomUUID()}`;
      this.sendSubscription(socket, subscription);
    }
    // Dispatch any frames the server pipelined into the ack's chunk *after* subscriptions are
    // replayed, so a pipelined `pub` is fanned out exactly as a steady-state frame would be (and
    // a pipelined `ack`/`err` settles its correlated RPC). Without this they would be silently
    // lost — they never reach `readUntilClosed`'s loop.
    for (const line of trailingLines) {
      const message = parseWireMessage(line);
      if (message !== undefined) {
        this.dispatch(message);
      }
    }
  }

  /** Send Hello and consume inbound frames until the matching Ack (success) or an Err
   * (version-mismatch → permanent) arrives; wake frames are skipped, exactly as the Python client
   * `continue`s past them. Reads through `this.lineBuffer` so a pipelined frame riding the ack's
   * chunk survives the handoff: this resolves with the **complete lines that followed the ack** in
   * that same chunk (the caller dispatches them through the steady-state path), while any trailing
   * partial remains in `this.lineBuffer` for the read loop to reassemble. */
  private handshakeExchange(
    socket: Socket,
    hello: HelloMessage,
    correlationId: string,
  ): Promise<string[]> {
    return new Promise<string[]>((resolve, reject) => {
      let settled = false;

      const settle = (action: () => void): void => {
        if (settled) {
          return;
        }
        settled = true;
        socket.removeListener('data', onData);
        socket.removeListener('error', onError);
        socket.removeListener('close', onClose);
        action();
      };

      const onData = (chunk: Buffer): void => {
        const lines = this.lineBuffer.push(chunk.toString('utf8'));
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i];
          if (line === undefined) {
            continue;
          }
          const message = parseWireMessage(line);
          if (message === undefined) {
            continue;
          }
          if (message.op === 'wake') {
            continue; // skip interleaved wake frames (Python `continue`s past them)
          }
          if (message.op === 'err') {
            settle(() =>
              reject(
                message.body.code === 'protocol_version_mismatch'
                  ? new ProtocolVersionMismatchError(message.body.message)
                  : new ConnectionLostError(`handshake rejected: ${message.body.message}`),
              ),
            );
            return;
          }
          if (message.op === 'ack' && message.correlation_id === correlationId) {
            // Hand the caller every complete line that followed the ack in this same chunk so it
            // is dispatched through the steady-state path instead of being dropped.
            settle(() => resolve(lines.slice(i + 1)));
            return;
          }
          // Any other op before the handshake completes is unexpected; ignore and keep waiting,
          // matching the Python loop which only acts on err/ack and skips the rest.
        }
      };
      const onError = (error: Error): void =>
        settle(() => reject(new ConnectionLostError(`handshake failed: ${error.message}`)));
      const onClose = (): void =>
        settle(() => reject(new ConnectionLostError('connection closed during handshake')));

      socket.on('data', onData);
      socket.on('error', onError);
      socket.on('close', onClose);
      this.writeMessage(socket, hello);
    });
  }

  /** Attach the steady-state read loop and resolve only when the socket closes (clean or error),
   * so the connect loop can decide whether to reconnect. */
  private readUntilClosed(): Promise<void> {
    const socket = this.socket;
    if (socket === undefined) {
      return Promise.reject(new ConnectionLostError('no socket to read'));
    }
    return new Promise<void>((resolve) => {
      const onData = (chunk: Buffer): void => {
        for (const line of this.lineBuffer.push(chunk.toString('utf8'))) {
          const message = parseWireMessage(line);
          if (message !== undefined) {
            this.dispatch(message);
          }
        }
      };
      const onClose = (): void => {
        socket.removeListener('data', onData);
        socket.removeListener('close', onClose);
        socket.removeListener('error', onError);
        resolve();
      };
      const onError = (error: Error): void => {
        this.logger.warn(`bus socket error: ${error.message}`);
        // 'error' is followed by 'close'; let onClose drive the reconnect decision.
      };
      socket.on('data', onData);
      socket.on('close', onClose);
      socket.on('error', onError);
    });
  }

  /** Route one steady-state inbound frame. `pub` → fan out to matching listeners; `ack`/`err` →
   * settle the correlated RPC; `wake` → skip (Python `continue`); other ops are ignored. */
  private dispatch(message: WireMessage): void {
    switch (message.op) {
      case 'pub':
        this.fanout(message.event);
        return;
      case 'ack':
        this.settleRpc(message.correlation_id, (rpc) => rpc.resolve(message.body.result ?? {}));
        return;
      case 'err':
        this.settleRpc(message.correlation_id, (rpc) =>
          rpc.reject(new Error(`rpc error [${message.body.code}]: ${message.body.message}`)),
        );
        return;
      // Skipped: wake (handshake-only hint, Python `continue`s) and any client→server ops the
      // server would never originate (hello/sub/rpc). No default action needed.
      case 'wake':
      case 'hello':
      case 'sub':
      case 'rpc':
        return;
      default:
        assertNever(message);
    }
  }

  /** Deliver a pushed event to every subscription whose filter matches (filter semantics mirror the
   * server's). */
  private fanout(event: BusEvent): void {
    for (const subscription of [...this.subscriptions]) {
      if (matchesFilter(event, subscription.filter)) {
        subscription.listener(event);
      }
    }
  }

  private settleRpc(correlationId: string, settle: (rpc: PendingRpc) => void): void {
    const rpc = this.pendingRpcs.get(correlationId);
    if (rpc === undefined) {
      return; // already timed out / unknown correlation — ignore
    }
    this.pendingRpcs.delete(correlationId);
    rpc.cancelTimeout();
    settle(rpc);
  }

  // === Low-level socket helpers ===============================================

  /** Wait for a live connection, kicking off connect if needed. The single place RPC/sub paths go
   * through so they never write to a not-yet-handshaked socket. */
  private async ensureConnected(): Promise<Socket> {
    if (this.state === 'closed') {
      throw new ConnectionLostError('client is closed');
    }
    await this.connect();
    if (this.state !== 'connected' || this.socket === undefined) {
      throw new ConnectionLostError('connection not established');
    }
    return this.socket;
  }

  private openSocket(): Promise<Socket> {
    return new Promise<Socket>((resolve, reject) => {
      const socket = netConnect(this.socketPath);
      const onConnect = (): void => {
        socket.removeListener('error', onConnectError);
        resolve(socket);
      };
      const onConnectError = (error: Error): void => {
        socket.removeListener('connect', onConnect);
        reject(new ConnectionLostError(`connect failed: ${error.message}`));
      };
      socket.once('connect', onConnect);
      socket.once('error', onConnectError);
    });
  }

  private sendSubscription(socket: Socket, subscription: Subscription): void {
    const message: SubMessage = {
      op: 'sub',
      schema_version: PROTOCOL_VERSION,
      correlation_id: subscription.correlationId,
      args: {
        filter: subscription.filter ?? {},
        presence_retain: false,
      },
    };
    this.writeMessage(socket, message);
  }

  /** Outbound JSON-lines framing: serialize the envelope and terminate with `\n` (Python's `send`). */
  private writeMessage(socket: Socket, message: WireMessage): void {
    socket.write(`${JSON.stringify(message)}\n`);
  }

  private nextBackoffMs(attempt: number): number {
    const exponential = Math.min(this.backoff.capMs, this.backoff.baseMs * 2 ** attempt);
    // Full jitter: a uniform draw across [0, window]. Spreads simultaneous reconnects.
    return this.clock.random() * exponential;
  }

  private failAllPendingRpcs(error: Error): void {
    for (const [, rpc] of this.pendingRpcs) {
      rpc.cancelTimeout();
      rpc.reject(error);
    }
    this.pendingRpcs.clear();
  }

  private teardownSocket(): void {
    if (this.socket !== undefined) {
      this.socket.removeAllListeners();
      this.socket.destroy();
      this.socket = undefined;
    }
  }
}

// === Module-private helpers ===================================================

/** Parse one JSON line into a {@link WireMessage}, or `undefined` if it is blank or unparseable.
 * Unlike the Python `validate_json` (which throws on a bad frame), we drop a malformed line and
 * keep reading — one corrupt frame must not tear down a long-lived connection. */
function parseWireMessage(line: string): WireMessage | undefined {
  const trimmed = line.trim();
  if (trimmed.length === 0) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (isWireMessage(parsed)) {
      return parsed;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

/** Narrow an unknown JSON value to a {@link WireMessage} by its `op` discriminant. The full payload
 * shape is trusted from the service (the protocol is the contract); this guards only that the frame
 * carries a known `op`, so a stray non-envelope line is dropped rather than mis-dispatched. */
function isWireMessage(value: unknown): value is WireMessage {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const op = (value as { op?: unknown }).op;
  return (
    op === 'hello' ||
    op === 'pub' ||
    op === 'sub' ||
    op === 'rpc' ||
    op === 'ack' ||
    op === 'err' ||
    op === 'wake'
  );
}

/**
 * Server-side {@link EventFilter} semantics, mirrored client-side for fanout on the multiplexed
 * connection: fields compose with AND; an absent filter field matches any. Identical to
 * `FakeBusClient`'s `matchesFilter` — kept private here to avoid the bus layer's only two impls
 * importing each other.
 */
function matchesFilter(event: BusEvent, filter: EventFilter | undefined): boolean {
  if (filter === undefined) {
    return true;
  }
  const record = event as unknown as Record<string, unknown>;
  return (
    fieldMatches(filter.role, record['role']) &&
    fieldMatches(filter.ticket_id, record['ticket_id']) &&
    fieldMatches(filter.type, event.type) &&
    fieldMatches(filter.entity, record['entity']) &&
    fieldMatches(filter.target_worker, record['target_worker']) &&
    fieldMatches(filter.kind, record['kind'])
  );
}

function fieldMatches<T>(expected: T | undefined, actual: unknown): boolean {
  return expected === undefined || expected === actual;
}

function stringifyError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Recognize the service's read-RPC envelope `{ ok: true, value: <dto> }` so {@link UdsBusClient.rpc}
 * can unwrap it to the bare DTO the store reads top-level. A reply that lacks a `value` key (a
 * write/command result, which is `{ ok, ...fields }`) is left untouched. See the unwrap call site.
 */
function isReadEnvelope(reply: Record<string, unknown>): reply is { value: unknown } {
  return 'value' in reply;
}

/** Compile-time exhaustiveness guard for the {@link WireMessage} switch: an un-handled `op` makes
 * this call a type error. */
function assertNever(_value: never): void {}

/**
 * Resolve the bus socket path from a runtime directory, mirroring the Python layout: the socket is
 * `<runtimeDir>/<SOCKET_RUNTIME_SUBDIR>/<SOCKET_BASENAME>`. Pass the same runtime dir the service
 * uses (per-repo); the caller wires this at app startup.
 */
export function defaultSocketPath(runtimeDir: string): string {
  return join(runtimeDir, SOCKET_RUNTIME_SUBDIR, SOCKET_BASENAME);
}
