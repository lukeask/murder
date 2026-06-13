/**
 * The browser {@link BusClient}: a single persistent WebSocket to the `/bus` bridge, speaking the
 * identical JSON-RPC protocol the {@link UdsBusClient} speaks over a Unix socket.
 *
 * This is the web counterpart of `@core/bus/UdsBusClient`. The Python bridge is a DUMB 1:1 relay:
 * it forwards each WS text frame to the unix socket (appending the `\n` the JSON-lines framing
 * wants) and forwards each unix-socket line back as one WS text frame. So the FULL protocol —
 * handshake, RPC correlation, subscription replay, reconnect/backoff — lives here, exactly as in
 * the UDS client. The store ({@link createAppStore}) is injected this client and never knows the
 * transport changed (the BusClient seam, README rule 4).
 *
 * ## Framing (the bridge contract)
 *
 * The browser side needs NO {@link LineBuffer}: WebSocket is message-framed, so each inbound text
 * frame is exactly one complete JSON envelope (`JSON.parse` directly) and each outbound envelope is
 * one `JSON.stringify(envelope)` text frame with NO trailing newline — the bridge adds the `\n`
 * when it writes to the unix socket. (Contrast the UDS client, which reassembles a `\n`-delimited
 * byte stream.) Otherwise this mirrors UdsBusClient frame-for-frame.
 *
 * ## Connection model — single multiplexed connection
 *
 * Same as UdsBusClient: one handshake (Hello/Ack), every RPC and every subscription rides the one
 * connection, RPCs paired to replies by `correlation_id`, inbound `pub` frames fanned out to the
 * listeners whose filter matches.
 *
 * ## Reconnect / backoff
 *
 * Exponential backoff with full jitter, capped (base 250ms, cap 10s). The attempt counter resets
 * after a successful handshake. A {@link ProtocolVersionMismatchError} is permanent — not retried.
 * {@link WsBusClient.close} stops reconnection for good.
 *
 * ## Error policy
 *
 * Identical to UdsBusClient: RPC rejects on timeout / `err` envelope / connection drop with the
 * call outstanding; subscriptions survive reconnect (re-sent after each handshake); the only fatal
 * non-retried condition is a protocol-version mismatch. The same connection-state hooks
 * ({@link onConnect}/{@link onDisconnect}/{@link onPermanentError}) are exposed off the interface.
 */

import type {
  BusClient,
  BusEventListener,
  RpcMethod,
  RpcParams,
  RpcResult,
  Unsubscribe,
} from '@core/bus/BusClient.js';
import { matchesFilter } from '@core/bus/matchesFilter.js';
import {
  type BusEvent,
  type ClientKind,
  DEFAULT_RPC_TIMEOUT_S,
  type EventFilter,
  type HelloMessage,
  PROTOCOL_VERSION,
  type RpcMessage,
  type SubMessage,
  type WireMessage,
} from '@core/bus/protocol.js';
import { unwrapReadReply } from '@core/bus/readEnvelope.js';

/** Minimal injected logger (mirrors UdsBusClient's `BusLogger`). `console` satisfies it. */
export interface BusLogger {
  warn(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
}

const SILENT_LOGGER: BusLogger = {
  warn: () => {},
  info: () => {},
};

/** Reconnect backoff parameters. Exponential with full jitter, capped. */
export interface BackoffConfig {
  baseMs: number;
  capMs: number;
}

const DEFAULT_BACKOFF: BackoffConfig = { baseMs: 250, capMs: 10_000 };

/** Injected timing seam so tests drive reconnect deterministically. Defaults to real timers. */
export interface Clock {
  sleep(ms: number): { promise: Promise<void>; cancel: () => void };
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

/** The browser `WebSocket` surface this client needs — narrowed so a test can inject a mock. The
 * real `WebSocket` constructor satisfies it. */
export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

/** Factory for a {@link WebSocketLike}, injected so tests pass a mock. Defaults to the global
 * `WebSocket`. The one place the transport is constructed. */
export type WebSocketFactory = (url: string) => WebSocketLike;

const REAL_WEBSOCKET_FACTORY: WebSocketFactory = (url) =>
  new WebSocket(url) as unknown as WebSocketLike;

/** The default `/bus` URL on the current origin (`ws://` or `wss://` to match `http(s)`). */
export function defaultBusUrl(): string {
  if (typeof location === 'undefined') {
    return 'ws://localhost/bus';
  }
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/bus`;
}

const CLIENT_ID_STORAGE_KEY = 'murder.web.client_id';

/** A stable client id persisted in localStorage so the supervisor can resume RPC/presence state
 * across reloads (mirrors UdsBusClient's stable `clientId`). Falls back to an in-memory id when
 * storage is unavailable (private mode / SSR). */
function loadOrCreateClientId(clientKind: ClientKind): string {
  const fresh = `${clientKind}-${cryptoRandomUUID()}`;
  try {
    const existing = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (existing !== null && existing.length > 0) {
      return existing;
    }
    localStorage.setItem(CLIENT_ID_STORAGE_KEY, fresh);
    return fresh;
  } catch {
    return fresh;
  }
}

/** `crypto.randomUUID` with a non-crypto fallback for ancient/insecure contexts. */
function cryptoRandomUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

export interface WsBusClientOptions {
  /** WebSocket URL for the bridge. Defaults to same-origin `/bus` ({@link defaultBusUrl}). */
  url?: string;
  /** Identifies this client to the supervisor; defaults to `'web'`. */
  clientKind?: ClientKind;
  /** Stable across reconnects/reloads; defaults to a localStorage-persisted `${clientKind}-${uuid}`. */
  clientId?: string;
  rpcTimeoutS?: number;
  backoff?: BackoffConfig;
  clock?: Clock;
  logger?: BusLogger;
  /** Injected for tests; defaults to the global `WebSocket`. */
  webSocketFactory?: WebSocketFactory;
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

/** Raised when the connection drops with an RPC outstanding, or before the handshake completes. */
export class ConnectionLostError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConnectionLostError';
  }
}

interface Subscription {
  readonly listener: BusEventListener;
  readonly filter: EventFilter | undefined;
  correlationId: string;
}

interface PendingRpc {
  resolve(result: Record<string, unknown>): void;
  reject(error: Error): void;
  cancelTimeout(): void;
}

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'closed';

export class WsBusClient implements BusClient {
  private readonly url: string;
  private readonly clientKind: ClientKind;
  private readonly clientId: string;
  private readonly rpcTimeoutS: number;
  private readonly backoff: BackoffConfig;
  private readonly clock: Clock;
  private readonly logger: BusLogger;
  private readonly makeSocket: WebSocketFactory;

  private state: ConnectionState = 'idle';
  private socket: WebSocketLike | undefined;

  private readonly subscriptions = new Set<Subscription>();
  private readonly pendingRpcs = new Map<string, PendingRpc>();

  private readonly connectListeners = new Set<() => void>();
  private readonly disconnectListeners = new Set<() => void>();
  private readonly permanentErrorListeners = new Set<(error: Error) => void>();

  private pendingSleep: { cancel: () => void } | undefined;
  private handshakeReady: Promise<void> | undefined;

  constructor(options: WsBusClientOptions = {}) {
    this.url = options.url ?? defaultBusUrl();
    this.clientKind = options.clientKind ?? 'web';
    this.clientId = options.clientId ?? loadOrCreateClientId(this.clientKind);
    this.rpcTimeoutS = options.rpcTimeoutS ?? DEFAULT_RPC_TIMEOUT_S;
    this.backoff = options.backoff ?? DEFAULT_BACKOFF;
    this.clock = options.clock ?? REAL_CLOCK;
    this.logger = options.logger ?? SILENT_LOGGER;
    this.makeSocket = options.webSocketFactory ?? REAL_WEBSOCKET_FACTORY;
  }

  /** {@inheritDoc UdsBusClient.connect} */
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
    const correlationId = `rpc-${cryptoRandomUUID()}`;
    const timeoutS = this.rpcTimeoutS;
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
    return unwrapReadReply(method, result) as RpcResult<M>;
  }

  /** {@inheritDoc BusClient.subscribe} */
  subscribe(listener: BusEventListener, filter?: EventFilter): Unsubscribe {
    const subscription: Subscription = {
      listener,
      filter,
      correlationId: `sub-${cryptoRandomUUID()}`,
    };
    this.subscriptions.add(subscription);
    if (this.state === 'connected' && this.socket !== undefined) {
      this.sendSubscription(this.socket, subscription);
    } else {
      void this.connect().catch(() => {});
    }
    return () => {
      this.subscriptions.delete(subscription);
      // LOCAL-only unsubscribe — the wire protocol has no `unsub` op (see UdsBusClient.subscribe).
    };
  }

  onConnect(listener: () => void): Unsubscribe {
    this.connectListeners.add(listener);
    if (this.state === 'connected') {
      listener();
    }
    return () => {
      this.connectListeners.delete(listener);
    };
  }

  onDisconnect(listener: () => void): Unsubscribe {
    this.disconnectListeners.add(listener);
    return () => {
      this.disconnectListeners.delete(listener);
    };
  }

  onPermanentError(listener: (error: Error) => void): Unsubscribe {
    this.permanentErrorListeners.add(listener);
    return () => {
      this.permanentErrorListeners.delete(listener);
    };
  }

  private notifyConnected(): void {
    for (const listener of [...this.connectListeners]) {
      try {
        listener();
      } catch {
        // a connect listener's failure is its own concern.
      }
    }
  }

  private notifyDisconnected(): void {
    for (const listener of [...this.disconnectListeners]) {
      try {
        listener();
      } catch {
        // a disconnect listener's failure is its own concern.
      }
    }
  }

  private notifyPermanentError(error: Error): void {
    for (const listener of [...this.permanentErrorListeners]) {
      try {
        listener(error);
      } catch {
        // a permanent-error listener's failure is its own concern.
      }
    }
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
    this.connectListeners.clear();
    this.disconnectListeners.clear();
    this.permanentErrorListeners.clear();
    this.teardownSocket();
  }

  private isClosed(): boolean {
    return this.state === 'closed';
  }

  // === Connection loop ========================================================

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
        let wasEstablished = false;
        try {
          await this.openAndHandshake();
          attempt = 0;
          if (!firstHandshakeSettled) {
            firstHandshakeSettled = true;
            resolveFirst();
          }
          wasEstablished = true;
          await this.readUntilClosed();
        } catch (error) {
          if (error instanceof ProtocolVersionMismatchError) {
            this.state = 'closed';
            if (!firstHandshakeSettled) {
              firstHandshakeSettled = true;
              rejectFirst(error);
            }
            this.failAllPendingRpcs(error);
            this.notifyPermanentError(error);
            return;
          }
          this.logger.warn(`bus connection error: ${stringifyError(error)}`);
        }
        if (this.isClosed()) {
          break;
        }
        this.failAllPendingRpcs(new ConnectionLostError('connection dropped'));
        if (wasEstablished) {
          this.notifyDisconnected();
        }
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

  /** Open the WS, send Hello, await the matching Ack (skipping wake frames), then mark connected
   * and replay every standing subscription onto the fresh connection. Inbound frames during the
   * handshake are dispatched through the same `onmessage` handler installed by {@link attachSocket},
   * so a `pub` that arrives right after the ack is fanned out normally (no separate buffer to drain
   * — WebSocket is already message-framed). */
  private openAndHandshake(): Promise<void> {
    this.state = 'connecting';
    const socket = this.makeSocket(this.url);
    this.socket = socket;

    const correlationId = `hello-${cryptoRandomUUID()}`;
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

    return new Promise<void>((resolve, reject) => {
      let handshakeDone = false;

      const finishHandshake = (): void => {
        handshakeDone = true;
        this.state = 'connected';
        for (const subscription of this.subscriptions) {
          subscription.correlationId = `sub-${cryptoRandomUUID()}`;
          this.sendSubscription(socket, subscription);
        }
        this.notifyConnected();
        resolve();
      };

      socket.onopen = (): void => {
        this.writeMessage(socket, hello);
      };
      // For an injected mock that is already "open" at construction, also send immediately.
      if (socket.readyState === WS_OPEN) {
        this.writeMessage(socket, hello);
      }

      socket.onmessage = (ev): void => {
        const message = parseWireMessage(ev.data);
        if (message === undefined) {
          return;
        }
        if (!handshakeDone) {
          if (message.op === 'wake') {
            return; // skip interleaved wake frames
          }
          if (message.op === 'err') {
            reject(
              message.body.code === 'protocol_version_mismatch'
                ? new ProtocolVersionMismatchError(message.body.message)
                : new ConnectionLostError(`handshake rejected: ${message.body.message}`),
            );
            return;
          }
          if (message.op === 'ack' && message.correlation_id === correlationId) {
            finishHandshake();
            return;
          }
          // Any other frame before the ack: ignore and keep waiting (mirrors UdsBusClient).
          return;
        }
        // Steady state: route the frame.
        this.dispatch(message);
      };

      socket.onerror = (): void => {
        if (!handshakeDone) {
          reject(new ConnectionLostError('handshake failed: socket error'));
        }
        // Post-handshake errors are followed by `onclose`, which drives the reconnect decision.
      };

      socket.onclose = (): void => {
        if (!handshakeDone) {
          reject(new ConnectionLostError('connection closed during handshake'));
          return;
        }
        // Established connection dropped: resolve the read loop so the connect loop reconnects.
        this.resolveRead?.();
      };
    });
  }

  /** Resolver for the steady-state read loop's "socket closed" promise. */
  private resolveRead: (() => void) | undefined;

  /** Resolve only when the (already-attached) socket closes, so the connect loop can decide whether
   * to reconnect. The `onmessage`/`onclose` handlers were installed in {@link openAndHandshake} and
   * stay attached; this just waits for the close signal that handler relays via {@link resolveRead}. */
  private readUntilClosed(): Promise<void> {
    if (this.socket === undefined) {
      return Promise.reject(new ConnectionLostError('no socket to read'));
    }
    return new Promise<void>((resolve) => {
      this.resolveRead = () => {
        this.resolveRead = undefined;
        resolve();
      };
    });
  }

  /** Route one steady-state inbound frame (mirrors UdsBusClient.dispatch). */
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
      case 'wake':
      case 'hello':
      case 'sub':
      case 'rpc':
        return;
      default:
        assertNever(message);
    }
  }

  private fanout(event: BusEvent): void {
    for (const subscription of [...this.subscriptions]) {
      if (matchesFilter(event, subscription.filter)) {
        try {
          subscription.listener(event);
        } catch {
          // a subscriber's failure is its own concern; never skip siblings.
        }
      }
    }
  }

  private settleRpc(correlationId: string, settle: (rpc: PendingRpc) => void): void {
    const rpc = this.pendingRpcs.get(correlationId);
    if (rpc === undefined) {
      return;
    }
    this.pendingRpcs.delete(correlationId);
    rpc.cancelTimeout();
    settle(rpc);
  }

  // === Low-level helpers ======================================================

  private async ensureConnected(): Promise<WebSocketLike> {
    if (this.state === 'closed') {
      throw new ConnectionLostError('client is closed');
    }
    await this.connect();
    if (this.state !== 'connected' || this.socket === undefined) {
      throw new ConnectionLostError('connection not established');
    }
    return this.socket;
  }

  private sendSubscription(socket: WebSocketLike, subscription: Subscription): void {
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

  /** Outbound framing: one envelope per WS text frame, NO trailing newline (the bridge appends the
   * `\n` when writing to the unix socket). */
  private writeMessage(socket: WebSocketLike, message: WireMessage): void {
    socket.send(JSON.stringify(message));
  }

  private nextBackoffMs(attempt: number): number {
    const exponential = Math.min(this.backoff.capMs, this.backoff.baseMs * 2 ** attempt);
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
      this.socket.onopen = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.onmessage = null;
      try {
        this.socket.close();
      } catch {
        // closing an already-dead socket is a no-op we don't care about.
      }
      this.socket = undefined;
    }
  }
}

// === Module-private helpers ===================================================

/** `WebSocket.OPEN` numeric constant (1), inlined so the module needs no DOM-global reference at
 * import time (jsdom / tests). */
const WS_OPEN = 1;

/** Parse one WS text frame (already one complete envelope) into a {@link WireMessage}, or
 * `undefined` if it is blank/unparseable/not an envelope. A corrupt frame is dropped, not fatal —
 * mirrors UdsBusClient's tolerant parse. */
function parseWireMessage(data: unknown): WireMessage | undefined {
  if (typeof data !== 'string') {
    return undefined;
  }
  const trimmed = data.trim();
  if (trimmed.length === 0) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    return isWireMessage(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

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

function stringifyError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function assertNever(_value: never): void {}
