/** Environment-neutral WebSocket transport for the generated application protocol. */

import type {
  ApplicationConnectionClient,
  CommandMethod,
  CommandParams,
  CommandResult,
  HydrateResult,
  ProjectionInvalidation,
  ProjectionInvalidationListener,
  ProjectionTopics,
  QueryMethod,
  QueryParams,
  QueryResult,
  TerminalFrameListener,
  TerminalAttachMode,
  TerminalInputBatch,
  TerminalInputLease,
  TerminalUpdate,
  Unsubscribe,
  ProjectionSnapshotListener,
} from './ApplicationClient.js';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ApplicationRequest,
  type ClientMessage,
  type ClientKind,
  type CommandName,
  type ErrorMessage,
  type PlanSeedFailedNotification,
  type ProjectionTopic,
  type QueryName,
  type ServerMessage,
  type SubscriptionSnapshot,
  type TerminalChunk,
  type TerminalFrame,
  type TerminalKeyframe,
} from '../generated/applicationProtocol.js';
import { unwrapReadReply } from './normalizeReply.js';

export interface ApplicationLogger {
  warn(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
}

const SILENT_LOGGER: ApplicationLogger = { warn: () => {}, info: () => {} };

export interface BackoffConfig {
  baseMs: number;
  capMs: number;
}

const DEFAULT_BACKOFF: BackoffConfig = { baseMs: 250, capMs: 10_000 };
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

export interface Clock {
  sleep(ms: number): { promise: Promise<void>; cancel: () => void };
  random(): number;
}

const REAL_CLOCK: Clock = {
  sleep(ms) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resolveSleep: (() => void) | undefined;
    const promise = new Promise<void>((resolve) => {
      resolveSleep = resolve;
      timer = setTimeout(resolve, ms);
    });
    return {
      promise,
      cancel: () => {
        if (timer !== undefined) clearTimeout(timer);
        resolveSleep?.();
      },
    };
  },
  random: Math.random,
};

export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

export interface ApplicationWebSocketClientOptions {
  readonly url: string;
  readonly clientId: string;
  readonly kind: ClientKind;
  readonly webSocketFactory: WebSocketFactory;
  requestTimeoutMs?: number;
  logger?: ApplicationLogger;
  backoff?: BackoffConfig;
  clock?: Clock;
  requestIdFactory?: () => string;
}

export class ProtocolVersionMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolVersionMismatchError';
  }
}

export class RpcTimeoutError extends Error {
  constructor(name: string, timeoutMs: number) {
    super(`request '${name}' timed out after ${timeoutMs}ms`);
    this.name = 'RpcTimeoutError';
  }
}

export class ConnectionLostError extends Error {
  constructor(message = 'application WebSocket connection lost') {
    super(message);
    this.name = 'ConnectionLostError';
  }
}

interface PendingRequest {
  readonly resolve: (result: Record<string, unknown>) => void;
  readonly reject: (error: Error) => void;
  readonly cancelTimeout: () => void;
}

interface ProjectionRegistration {
  readonly id: string;
  readonly topics: readonly ProjectionTopic[];
  readonly invalidationListener: ProjectionInvalidationListener | undefined;
  /** Cursor supplied to the next server subscription. */
  resumeCursor: number | null;
  /** Highest cursor whose invalidation was delivered locally. */
  lastDeliveredCursor: number | null;
  readonly useHelloCursor: boolean;
  initialSettled: boolean;
  ready: boolean;
  tailBuffer: Array<{ cursor: number | null; payload: unknown }>;
  readonly snapshotListener: ProjectionSnapshotListener | undefined;
  readonly resolveInitial: (result: HydrateResult) => void;
  readonly rejectInitial: (error: Error) => void;
}

interface TerminalRegistration {
  readonly id: string;
  readonly sessionId: string | null;
  readonly listener: TerminalFrameListener;
  lastSequence: number;
  suspended: boolean;
  resyncPending: boolean;
  mode: TerminalAttachMode;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'closed' | 'permanent-error';

export class ApplicationWebSocketClient implements ApplicationConnectionClient {
  private readonly url: string;
  private readonly clientId: string;
  private readonly kind: ClientKind;
  private readonly requestTimeoutMs: number;
  private readonly logger: ApplicationLogger;
  private readonly backoff: BackoffConfig;
  private readonly clock: Clock;
  private readonly makeSocket: WebSocketFactory;
  private readonly makeRequestId: () => string;

  private state: ConnectionState = 'idle';
  private socket: WebSocketLike | undefined;
  private loop: Promise<void> | undefined;
  private abortHandshake: ((error: Error) => void) | undefined;
  private socketClosed: Promise<void> | undefined;
  private resolveSocketClosed: (() => void) | undefined;
  private cancelledSleep: (() => void) | undefined;
  private permanentError: Error | undefined;

  private readonly pendingRequests = new Map<string, PendingRequest>();
  private readonly projections = new Map<string, ProjectionRegistration>();
  private readonly terminals = new Map<string, TerminalRegistration>();
  private readonly terminalInputWatchers = new Map<string, Set<(error: Error) => void>>();
  private readonly connectWaiters = new Set<{
    resolve: () => void;
    reject: (error: Error) => void;
  }>();

  private readonly connectListeners = new Set<() => void>();
  private readonly disconnectListeners = new Set<() => void>();
  private readonly permanentErrorListeners = new Set<(error: Error) => void>();
  private readonly planSeedFailedListeners = new Set<
    (notification: PlanSeedFailedNotification) => void
  >();

  /** Last `server.hello.fact_cursor` watermark; `undefined` until the first hello. */
  private factCursor: number | undefined;
  /** Last `server.hello.projection_cursor` watermark; `undefined` until the first hello. */
  private projectionCursor: number | undefined;
  private serverId: string | undefined;

  constructor(options: ApplicationWebSocketClientOptions) {
    this.url = options.url;
    this.clientId = options.clientId;
    this.kind = options.kind;
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    this.logger = options.logger ?? SILENT_LOGGER;
    this.backoff = options.backoff ?? DEFAULT_BACKOFF;
    this.clock = options.clock ?? REAL_CLOCK;
    this.makeSocket = options.webSocketFactory;
    this.makeRequestId = options.requestIdFactory ?? fallbackId;
  }

  connect(): Promise<void> {
    if (this.state === 'connected') return Promise.resolve();
    if (this.state === 'closed') return Promise.reject(new ConnectionLostError('client is closed'));
    if (this.permanentError !== undefined) return Promise.reject(this.permanentError);
    if (this.loop === undefined) {
      this.loop = this.runConnectionLoop().finally(() => {
        this.loop = undefined;
      });
    }
    return new Promise<void>((resolve, reject) => {
      this.connectWaiters.add({ resolve, reject });
    });
  }

  /** Fact-log watermark from the most recent `server.hello`, or `undefined` pre-handshake. */
  getFactCursor(): number | undefined {
    return this.factCursor;
  }

  /** Projection-input watermark from the most recent `server.hello`, or `undefined` pre-handshake. */
  getProjectionCursor(): number | undefined {
    return this.projectionCursor;
  }

  close(): void {
    if (this.state === 'closed') return;
    this.state = 'closed';
    this.cancelledSleep?.();
    this.cancelledSleep = undefined;
    const error = new ConnectionLostError('client is closed');
    this.abortHandshake?.(error);
    this.abortHandshake = undefined;
    this.resolveSocketClosed?.();
    this.resolveSocketClosed = undefined;
    this.teardownSocket();
    this.failAllRequests(error);
    this.rejectConnectWaiters(error);
    for (const projection of this.projections.values()) {
      if (!projection.initialSettled) projection.rejectInitial(error);
    }
    this.projections.clear();
    this.terminals.clear();
  }

  async query<M extends QueryMethod>(
    name: M,
    params: QueryParams<M>,
  ): Promise<QueryResult<M>> {
    const result = await this.request(
      'query',
      name,
      params as Record<string, unknown>,
    );
    return unwrapReadReply(name, result) as QueryResult<M>;
  }

  async command<M extends CommandMethod>(
    name: M,
    params: CommandParams<M>,
  ): Promise<CommandResult<M>> {
    return (await this.request(
      'command',
      name,
      params as Record<string, unknown>,
    )) as CommandResult<M>;
  }

  hydrate(
    topics: ProjectionTopics,
    invalidationListener?: ProjectionInvalidationListener,
    since?: number | null,
    snapshotListener?: ProjectionSnapshotListener,
  ): Promise<HydrateResult> {
    const normalized = typeof topics === 'string' ? [topics] : [...topics];
    const id = `projection-${fallbackId()}`;
    let resolveInitial!: (result: HydrateResult) => void;
    let rejectInitial!: (error: Error) => void;
    const result = new Promise<HydrateResult>((resolve, reject) => {
      resolveInitial = resolve;
      rejectInitial = reject;
    });
    this.projections.set(id, {
      id,
      topics: normalized,
      invalidationListener,
      resumeCursor: typeof since === 'number' ? since : null,
      lastDeliveredCursor: null,
      useHelloCursor: since === undefined,
      initialSettled: false,
      ready: false,
      tailBuffer: [],
      snapshotListener,
      resolveInitial,
      rejectInitial,
    });
    if (this.state === 'connected' && this.socket !== undefined) {
      this.sendProjection(this.socket, this.projections.get(id));
    } else {
      void this.connect().catch((error: unknown) => {
        const current = this.projections.get(id);
        if (current !== undefined && !current.initialSettled) {
          current.rejectInitial(asError(error));
          this.projections.delete(id);
        }
      });
    }
    return result;
  }

  attachTerminal(
    sessionId: string | null,
    listener: TerminalFrameListener,
    mode: TerminalAttachMode = 'raw',
  ): Unsubscribe {
    const id = `terminal-${fallbackId()}`;
    const registration: TerminalRegistration = {
      id,
      sessionId,
      listener,
      lastSequence: 0,
      suspended: false,
      resyncPending: false,
      mode,
    };
    this.terminals.set(id, registration);
    if (this.state === 'connected' && this.socket !== undefined) {
      this.sendTerminalAttach(this.socket, registration);
    } else {
      void this.connect().catch(() => {});
    }
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      this.detachTerminal(id);
    };
  }

  async openTerminalInput(sessionId: string): Promise<TerminalInputLease> {
    const result = await this.command('session.writer.acquire', {
      session_id: sessionId,
      mode: 'raw_terminal',
      holder: { kind: 'client', id: this.clientId },
    });
    if (!('lease' in result)) throw new Error(`terminal input unavailable: ${result.reason}`);
    return {
      streamId: `terminal-input-${fallbackId()}`,
      sessionId,
      leaseId: result.lease.lease_id,
      fence: result.lease.fence,
    };
  }

  async renewTerminalInput(lease: TerminalInputLease): Promise<TerminalInputLease> {
    const result = await this.command('session.writer.renew', {
      session_id: lease.sessionId,
      lease_id: lease.leaseId,
      fence: lease.fence,
      holder: { kind: 'client', id: this.clientId },
    });
    if (!('lease' in result)) throw new Error(`terminal input unavailable: ${result.reason}`);
    return { ...lease, leaseId: result.lease.lease_id, fence: result.lease.fence };
  }

  async closeTerminalInput(lease: TerminalInputLease): Promise<void> {
    try {
      await this.command('session.writer.release', {
        session_id: lease.sessionId,
        lease_id: lease.leaseId,
        fence: lease.fence,
        holder: { kind: 'client', id: this.clientId },
        reason: 'interactive terminal detached',
      });
    } finally {
      if (this.state === 'connected' && this.socket !== undefined) {
        this.write(this.socket, { op: 'terminal.input_detach', stream_id: lease.streamId });
      }
    }
  }

  sendTerminalInput(batch: TerminalInputBatch): boolean {
    if (this.state !== 'connected' || this.socket === undefined) return false;
    try {
      this.write(this.socket, {
        op: 'terminal.input',
        stream_id: batch.streamId,
        session_id: batch.sessionId,
        lease_id: batch.leaseId,
        fence: batch.fence,
        input_sequence: batch.inputSequence,
        encoding: 'base64',
        data: batch.data,
      });
      return true;
    } catch {
      return false;
    }
  }

  watchTerminalInput(streamId: string, listener: (error: Error) => void): Unsubscribe {
    const listeners = this.terminalInputWatchers.get(streamId) ?? new Set();
    listeners.add(listener);
    this.terminalInputWatchers.set(streamId, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) this.terminalInputWatchers.delete(streamId);
    };
  }

  private detachTerminal(streamId: string): void {
    if (!this.terminals.delete(streamId)) return;
    if (this.state === 'connected' && this.socket !== undefined) {
      this.write(this.socket, { op: 'terminal.detach', stream_id: streamId });
    }
  }

  onConnect(listener: () => void): Unsubscribe {
    this.connectListeners.add(listener);
    if (this.state === 'connected') queueMicrotask(listener);
    return () => this.connectListeners.delete(listener);
  }

  onDisconnect(listener: () => void): Unsubscribe {
    this.disconnectListeners.add(listener);
    return () => this.disconnectListeners.delete(listener);
  }

  onPermanentError(listener: (error: Error) => void): Unsubscribe {
    this.permanentErrorListeners.add(listener);
    if (this.permanentError !== undefined) {
      const error = this.permanentError;
      queueMicrotask(() => this.safelyNotify(() => listener(error)));
    }
    return () => this.permanentErrorListeners.delete(listener);
  }

  onPlanSeedFailed(listener: (notification: PlanSeedFailedNotification) => void): Unsubscribe {
    this.planSeedFailedListeners.add(listener);
    return () => this.planSeedFailedListeners.delete(listener);
  }

  private async request(
    kind: 'query' | 'command',
    name: QueryName | CommandName,
    params: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const socket = await this.ensureConnected();
    const requestId = this.makeRequestId();
    const timeoutMs = this.requestTimeoutMs;
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRequests.delete(requestId);
        reject(new RpcTimeoutError(name, timeoutMs));
      }, timeoutMs);
      this.pendingRequests.set(requestId, {
        resolve,
        reject,
        cancelTimeout: () => clearTimeout(timer),
      });
      this.write(socket, {
        op: 'request',
        request_id: requestId,
        request: { kind, name, params } as ApplicationRequest,
        timeout_s: Math.ceil(timeoutMs / 1000),
      });
    });
  }

  private async ensureConnected(): Promise<WebSocketLike> {
    await this.connect();
    if (this.state !== 'connected' || this.socket === undefined) {
      throw new ConnectionLostError();
    }
    return this.socket;
  }

  private async runConnectionLoop(): Promise<void> {
    let attempt = 0;
    while (this.state !== 'closed' && this.state !== 'permanent-error') {
      try {
        await this.openAndHandshake();
        attempt = 0;
        this.state = 'connected';
        this.resolveConnectWaiters();
        this.resendStreams();
        for (const listener of [...this.connectListeners]) this.safelyNotify(listener);
        await this.waitForClose();
        if ((this.state as ConnectionState) === 'closed') return;
        this.state = 'connecting';
        this.failAllRequests(new ConnectionLostError());
        for (const terminal of this.terminals.values()) {
          terminal.resyncPending = false;
          if (terminal.lastSequence > 0) terminal.suspended = true;
        }
        for (const listener of [...this.disconnectListeners]) this.safelyNotify(listener);
      } catch (error: unknown) {
        const normalized = asError(error);
        this.teardownSocket();
        if (normalized instanceof ProtocolVersionMismatchError) {
          this.state = 'permanent-error';
          this.permanentError = normalized;
          this.failAllRequests(normalized);
          this.rejectConnectWaiters(normalized);
          for (const projection of this.projections.values()) {
            if (!projection.initialSettled) projection.rejectInitial(normalized);
          }
          for (const listener of [...this.permanentErrorListeners])
            this.safelyNotify(() => listener(normalized));
          return;
        }
        this.state = 'connecting';
        this.rejectConnectWaiters(normalized);
        this.logger.warn('application WebSocket connection failed; retrying', normalized);
      } finally {
        this.teardownSocket();
      }

      if (
        (this.state as ConnectionState) === 'closed' ||
        (this.state as ConnectionState) === 'permanent-error'
      ) {
        return;
      }
      const sleep = this.clock.sleep(this.nextBackoffMs(attempt++));
      this.cancelledSleep = sleep.cancel;
      await sleep.promise;
      this.cancelledSleep = undefined;
    }
  }

  private openAndHandshake(): Promise<void> {
    this.state = 'connecting';
    const socket = this.makeSocket(this.url);
    this.socket = socket;
    this.socketClosed = new Promise<void>((resolve) => {
      this.resolveSocketClosed = resolve;
    });
    let settled = false;
    return new Promise<void>((resolve, reject) => {
      const fail = (error: Error): void => {
        if (settled) return;
        settled = true;
        this.abortHandshake = undefined;
        reject(error);
      };
      this.abortHandshake = fail;
      const sendHello = (): void => {
        this.write(socket, {
          op: 'client.hello',
          protocol_version: APPLICATION_PROTOCOL_VERSION,
          client: { client_id: this.clientId, kind: this.kind },
        });
      };
      socket.onopen = sendHello;
      if (socket.readyState === WS_OPEN) sendHello();
      socket.onmessage = (event): void => {
        const message = parseServerMessage(event.data);
        if (message === undefined) return;
        if (!settled) {
          if (message.op === 'error') {
            fail(this.errorFromMessage(message));
          } else if (message.op === 'server.hello') {
            if (message.protocol_version !== APPLICATION_PROTOCOL_VERSION) {
              fail(
                new ProtocolVersionMismatchError(
                  `server protocol ${message.protocol_version}; client protocol ${APPLICATION_PROTOCOL_VERSION}`,
                ),
              );
            } else {
              const serverChanged = this.serverId !== undefined && this.serverId !== message.server_id;
              this.serverId = message.server_id;
              this.factCursor = message.fact_cursor;
              this.projectionCursor = message.projection_cursor;
              if (serverChanged) {
                for (const terminal of this.terminals.values()) {
                  terminal.lastSequence = 0;
                  terminal.suspended = false;
                  terminal.resyncPending = false;
                }
              }
              settled = true;
              this.abortHandshake = undefined;
              resolve();
            }
          }
          return;
        }
        this.dispatch(message);
      };
      socket.onerror = (): void => fail(new ConnectionLostError('handshake failed'));
      socket.onclose = (): void => {
        if (!settled) {
          fail(new ConnectionLostError('connection closed during handshake'));
        }
        this.resolveSocketClosed?.();
        this.resolveSocketClosed = undefined;
      };
    });
  }

  private waitForClose(): Promise<void> {
    return this.socketClosed ?? Promise.resolve();
  }

  private dispatch(message: ServerMessage): void {
    switch (message.op) {
      case 'reply':
        this.settleRequest(message.request_id, (pending) => pending.resolve(message.result));
        return;
      case 'notification':
        if (message.type === 'plan.seed_failed') {
          for (const listener of [...this.planSeedFailedListeners]) {
            this.safelyNotify(() => listener(message));
          }
        }
        return;
      case 'subscription.ready':
        this.acceptProjectionReady(message.subscription_id, message.snapshot);
        return;
      case 'subscription.event':
        this.acceptProjectionEvent(message.subscription_id, message.cursor, message.payload);
        return;
      case 'terminal.frame': {
        const stream = this.terminals.get(message.stream_id);
        if (stream !== undefined) {
          this.acceptTerminalFrame(stream, message.frame);
        }
        return;
      }
      case 'terminal.keyframe': {
        const stream = this.terminals.get(message.stream_id);
        if (stream !== undefined) {
          this.acceptTerminalKeyframe(stream, message.keyframe, false);
        }
        return;
      }
      case 'terminal.chunk': {
        const stream = this.terminals.get(message.stream_id);
        if (stream !== undefined) {
          this.acceptTerminalChunk(stream, message.chunk);
        }
        return;
      }
      case 'terminal.gap': {
        const stream = this.terminals.get(message.stream_id);
        if (stream !== undefined) {
          stream.suspended = true;
          this.notifyTerminal(stream, message.gap);
          this.requestTerminalResync(stream, 'gap');
        }
        return;
      }
      case 'terminal.resynced': {
        const stream = this.terminals.get(message.stream_id);
        if (stream !== undefined) {
          this.acceptTerminalKeyframe(stream, message.keyframe, true);
        }
        return;
      }
      case 'error':
        this.acceptError(message);
        return;
      case 'server.hello':
      case 'terminal.attached':
        return;
    }
  }

  private acceptProjectionReady(id: string, snapshot: SubscriptionSnapshot): void {
    const projection = this.findProjection(id);
    if (projection === undefined) return;
    projection.resumeCursor = Math.max(projection.resumeCursor ?? 0, snapshot.cursor);
    for (const item of snapshot.replay) {
      this.deliverProjectionPayload(projection, item.cursor, item.payload);
    }
    projection.ready = true;
    for (const buffered of projection.tailBuffer) {
      this.deliverProjectionPayload(projection, buffered.cursor, buffered.payload);
    }
    projection.tailBuffer = [];
    if (!projection.initialSettled) {
      projection.initialSettled = true;
      projection.resolveInitial({
        snapshots: snapshot.snapshots,
        cursor: snapshot.cursor,
        mode: snapshot.mode,
        unsubscribe: this.projectionDisposer(id),
      });
    } else {
      this.safelyNotify(() => projection.snapshotListener?.({
        snapshots: snapshot.snapshots,
        cursor: snapshot.cursor,
        mode: snapshot.mode,
      }));
    }
  }

  private acceptProjectionEvent(
    id: string,
    cursor: number | null | undefined,
    payload: unknown,
  ): void {
    const projection = this.findProjection(id);
    if (projection === undefined) return;
    if (!projection.ready) {
      projection.tailBuffer.push({ cursor: cursor ?? null, payload });
      return;
    }
    this.deliverProjectionPayload(projection, cursor, payload);
  }

  private deliverProjectionPayload(
    projection: ProjectionRegistration,
    cursor: number | null | undefined,
    payload: unknown,
  ): void {
    if (cursor !== null && cursor !== undefined) {
      projection.resumeCursor = Math.max(projection.resumeCursor ?? 0, cursor);
    }
    if (isProjectionInvalidation(payload)) {
      if (cursor !== null && cursor !== undefined) {
        if (cursor <= (projection.lastDeliveredCursor ?? -1)) return;
        projection.lastDeliveredCursor = cursor;
      }
      this.notifyProjectionInvalidation(projection, payload);
    }
  }

  private notifyProjectionInvalidation(
    projection: ProjectionRegistration,
    invalidation: ProjectionInvalidation,
  ): void {
    try {
      projection.invalidationListener?.(invalidation);
    } catch {
      // A projection consumer owns its own error state.
    }
  }

  private projectionDisposer(id: string): Unsubscribe {
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      const projection = this.projections.get(id);
      if (projection === undefined) return;
      this.projections.delete(id);
      if (this.state === 'connected' && this.socket !== undefined) {
        this.write(this.socket, { op: 'unsubscribe', subscription_id: id });
      }
    };
  }

  private acceptError(message: ErrorMessage): void {
    const error = this.errorFromMessage(message);
    if (message.request_id !== null && message.request_id !== undefined) {
      this.settleRequest(message.request_id, (pending) => pending.reject(error));
    }
    if (message.subscription_id !== null && message.subscription_id !== undefined) {
      const projection = this.findProjection(message.subscription_id);
      if (projection !== undefined && !projection.initialSettled) {
        projection.rejectInitial(error);
        this.projections.delete(projection.id);
      }
    }
    if (message.stream_id !== null && message.stream_id !== undefined) {
      this.terminalInputWatchers
        .get(message.stream_id)
        ?.forEach((listener) => this.safelyNotify(() => listener(error)));
      // Keep attach intent across transient stream errors so reconnect
      // reattaches. Only `stream_failed` means the registration should be abandoned.
      if (message.error.code === 'stream_failed') {
        this.terminals.delete(message.stream_id);
      } else {
        this.logger.warn(error.message);
      }
    }
  }

  private errorFromMessage(message: ErrorMessage): Error {
    if (message.error.code === 'version_mismatch') {
      return new ProtocolVersionMismatchError(message.error.message);
    }
    return new Error(
      `application error [${message.error.code}]: ${message.error.message}`,
    );
  }

  private settleRequest(id: string, settle: (pending: PendingRequest) => void): void {
    const pending = this.pendingRequests.get(id);
    if (pending === undefined) return;
    this.pendingRequests.delete(id);
    pending.cancelTimeout();
    settle(pending);
  }

  private resendStreams(): void {
    const socket = this.socket;
    if (socket === undefined) return;
    for (const projection of this.projections.values()) this.sendProjection(socket, projection);
    for (const terminal of this.terminals.values()) this.sendTerminalAttach(socket, terminal);
  }

  private sendProjection(
    socket: WebSocketLike,
    projection: ProjectionRegistration | undefined,
  ): void {
    if (projection === undefined) return;
    projection.ready = false;
    projection.tailBuffer = [];
    const projectionSubscription: {
      kind: 'projections';
      topics: readonly ProjectionTopic[];
      cursor?: number;
    } = {
      kind: 'projections',
      topics: projection.topics,
    };
    const cursor = projection.useHelloCursor && projection.resumeCursor === null
      ? this.projectionCursor
      : projection.resumeCursor;
    if (cursor !== null && cursor !== undefined) {
      projectionSubscription.cursor = cursor;
    }
    this.write(socket, {
      op: 'subscribe',
      subscription_id: projection.id,
      subscription: projectionSubscription,
    });
  }

  private findProjection(subscriptionId: string): ProjectionRegistration | undefined {
    return this.projections.get(subscriptionId);
  }

  private sendTerminalAttach(socket: WebSocketLike, terminal: TerminalRegistration): void {
    if (terminal.sessionId === null) return;
    this.write(socket, {
      op: 'terminal.attach',
      stream_id: terminal.id,
      target: { session_id: terminal.sessionId },
      after_sequence: terminal.lastSequence,
      mode: terminal.mode,
    });
    if (terminal.lastSequence > 0) this.requestTerminalResync(terminal, 'reconnect');
  }

  private acceptTerminalFrame(terminal: TerminalRegistration, frame: TerminalFrame): void {
    if (frame.sequence <= terminal.lastSequence) return;
    if (!frame.reset) {
      this.requestTerminalResync(terminal, 'unsupported_mode');
      return;
    }
    terminal.lastSequence = frame.sequence;
    terminal.suspended = false;
    terminal.resyncPending = false;
    this.notifyTerminal(terminal, frame);
  }

  private acceptTerminalKeyframe(
    terminal: TerminalRegistration,
    keyframe: TerminalKeyframe,
    resynced: boolean,
  ): void {
    if (keyframe.sequence <= terminal.lastSequence) return;
    terminal.lastSequence = keyframe.sequence;
    terminal.suspended = false;
    terminal.resyncPending = false;
    this.notifyTerminal(
      terminal,
      resynced ? { type: 'terminal.resynced', keyframe } : keyframe,
    );
  }

  private acceptTerminalChunk(terminal: TerminalRegistration, chunk: TerminalChunk): void {
    if (chunk.sequence <= terminal.lastSequence || terminal.suspended) return;
    const expected = terminal.lastSequence + 1;
    if (chunk.sequence !== expected) {
      terminal.suspended = true;
      this.notifyTerminal(terminal, {
        type: 'terminal.gap',
        expected_sequence: expected,
        next_sequence: chunk.sequence,
      });
      this.requestTerminalResync(terminal, 'gap');
      return;
    }
    terminal.lastSequence = chunk.sequence;
    this.notifyTerminal(terminal, chunk);
  }

  private requestTerminalResync(
    terminal: TerminalRegistration,
    reason: 'gap' | 'reconnect' | 'unsupported_mode',
  ): void {
    if (terminal.resyncPending || this.state !== 'connected' || this.socket === undefined) return;
    terminal.resyncPending = true;
    this.write(this.socket, {
      op: 'terminal.resync',
      stream_id: terminal.id,
      after_sequence: terminal.lastSequence,
      request: 'keyframe',
      reason,
    });
  }

  private notifyTerminal(
    terminal: TerminalRegistration,
    update: TerminalUpdate,
  ): void {
    try {
      terminal.listener(update);
    } catch {
      // A terminal renderer cannot disrupt sibling streams or transport dispatch.
    }
  }

  private safelyNotify(listener: () => void): void {
    try {
      listener();
    } catch (error) {
      this.logger.warn('application WebSocket listener failed', asError(error));
    }
  }

  private write(socket: WebSocketLike, message: ClientMessage): void {
    socket.send(JSON.stringify(message));
  }

  private nextBackoffMs(attempt: number): number {
    const maximum = Math.min(this.backoff.capMs, this.backoff.baseMs * 2 ** attempt);
    return this.clock.random() * maximum;
  }

  private resolveConnectWaiters(): void {
    for (const waiter of this.connectWaiters) waiter.resolve();
    this.connectWaiters.clear();
  }

  private rejectConnectWaiters(error: Error): void {
    for (const waiter of this.connectWaiters) waiter.reject(error);
    this.connectWaiters.clear();
  }

  private failAllRequests(error: Error): void {
    for (const pending of this.pendingRequests.values()) {
      pending.cancelTimeout();
      pending.reject(error);
    }
    this.pendingRequests.clear();
  }

  private teardownSocket(): void {
    const socket = this.socket;
    if (socket === undefined) return;
    socket.onopen = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    try {
      socket.close();
    } catch {
      // Closing an already-dead browser socket is harmless.
    }
    this.socket = undefined;
  }
}

const WS_OPEN = 1;

const PROJECTION_TOPICS: ReadonlySet<string> = new Set([
  'conversations',
  'roster',
  'schedule',
  'favorites',
  'templates',
  'themes',
  'workflows',
  'workflow_runs',
  'activities',
  'settings',
  'approvals',
  'permissions',
  'sessions',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isProjectionInvalidation(value: unknown): value is ProjectionInvalidation {
  if (!isRecord(value)) return false;
  const { type, projection, subject_key, generation, source_fact_id } = value;
  return (
    type === 'projection.invalidate' &&
    typeof projection === 'string' &&
    PROJECTION_TOPICS.has(projection) &&
    typeof subject_key === 'string' &&
    subject_key.length > 0 &&
    typeof generation === 'number' &&
    Number.isSafeInteger(generation) &&
    generation >= 0 &&
    (source_fact_id === undefined ||
      source_fact_id === null ||
      (typeof source_fact_id === 'string' && UUID_RE.test(source_fact_id)))
  );
}

function decodeSocketData(data: unknown): string | undefined {
  if (typeof data === 'string') return data;
  // Node's undici/ws stacks may deliver text frames as Buffer / Uint8Array / ArrayBuffer.
  // Ignoring those makes handshake hang until close, which looks like a reconnect storm.
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer(data)) {
    return data.toString('utf8');
  }
  if (data instanceof ArrayBuffer) {
    return new TextDecoder().decode(data);
  }
  if (data instanceof Uint8Array) {
    return new TextDecoder().decode(data);
  }
  return undefined;
}

function parseServerMessage(data: unknown): ServerMessage | undefined {
  const text = decodeSocketData(data);
  if (text === undefined || text.trim() === '') return undefined;
  try {
    const parsed = JSON.parse(text) as { op?: unknown };
    return isServerOp(parsed.op) ? (parsed as ServerMessage) : undefined;
  } catch {
    return undefined;
  }
}

function isServerOp(op: unknown): boolean {
  return (
    op === 'server.hello' ||
    op === 'reply' ||
    op === 'notification' ||
    op === 'subscription.ready' ||
    op === 'subscription.event' ||
    op === 'terminal.attached' ||
    op === 'terminal.frame' ||
    op === 'terminal.keyframe' ||
    op === 'terminal.chunk' ||
    op === 'terminal.gap' ||
    op === 'terminal.resynced' ||
    op === 'error'
  );
}

function fallbackId(): string {
  try {
    return globalThis.crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

export type { ProjectionTopic };
