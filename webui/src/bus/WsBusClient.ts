/**
 * Browser transport for Murder's public application protocol.
 *
 * The WebSocket bridge is deliberately framing-only: this client owns hello/version negotiation,
 * request correlation, resumable projection subscriptions, and independent terminal streams.
 */

import type {
  BusClient,
  BusEventListener,
  CommandMethod,
  CommandParams,
  CommandResult,
  HydrateResult,
  ProjectionTopics,
  QueryMethod,
  QueryParams,
  QueryResult,
  TerminalFrameListener,
  Unsubscribe,
} from '@core/bus/BusClient.js';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ClientMessage,
  type CommandName,
  type ErrorMessage,
  type ProjectionTopic,
  type QueryName,
  type ServerMessage,
  type SubscriptionSnapshot,
} from '@core/generated/applicationProtocol.js';
import type { BusEvent } from '@core/bus/protocol.js';
import { isBusEvent } from '@core/bus/matchesFilter.js';
import { unwrapReadReply } from '@core/bus/readEnvelope.js';

export interface BusLogger {
  warn(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
}

const SILENT_LOGGER: BusLogger = { warn: () => {}, info: () => {} };

export interface BackoffConfig {
  baseMs: number;
  capMs: number;
}

const DEFAULT_BACKOFF: BackoffConfig = { baseMs: 250, capMs: 10_000 };
const DEFAULT_REQUEST_TIMEOUT_S = 30;

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

const REAL_WEBSOCKET_FACTORY: WebSocketFactory = (url) =>
  new WebSocket(url) as unknown as WebSocketLike;

export function defaultBusUrl(): string {
  if (typeof location === 'undefined') return 'ws://localhost/api/ws';
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/api/ws`;
}

const CLIENT_ID_STORAGE_KEY = 'murder.web.client_id';

function stableClientId(): string {
  try {
    const stored = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (stored !== null && stored !== '') return stored;
    const created = `web-${randomId()}`;
    localStorage.setItem(CLIENT_ID_STORAGE_KEY, created);
    return created;
  } catch {
    return `web-${randomId()}`;
  }
}

export interface WsBusClientOptions {
  url?: string;
  clientId?: string;
  requestTimeoutS?: number;
  logger?: BusLogger;
  backoff?: BackoffConfig;
  clock?: Clock;
  webSocketFactory?: WebSocketFactory;
}

export class ProtocolVersionMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolVersionMismatchError';
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
  readonly notificationId: string;
  readonly topics: readonly ProjectionTopic[];
  readonly listener: BusEventListener | undefined;
  cursor: number | null;
  notificationCursor: number | null;
  initialSettled: boolean;
  ready: boolean;
  tailBuffer: Array<{ cursor: number | null; payload: Record<string, unknown> }>;
  readonly resolveInitial: (result: HydrateResult) => void;
  readonly rejectInitial: (error: Error) => void;
}

interface TerminalRegistration {
  readonly id: string;
  readonly sessionId: string | null;
  readonly listener: TerminalFrameListener;
}

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'closed' | 'permanent-error';

export class WsBusClient implements BusClient {
  private readonly url: string;
  private readonly clientId: string;
  private readonly requestTimeoutS: number;
  private readonly logger: BusLogger;
  private readonly backoff: BackoffConfig;
  private readonly clock: Clock;
  private readonly makeSocket: WebSocketFactory;

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
  private readonly connectWaiters = new Set<{
    resolve: () => void;
    reject: (error: Error) => void;
  }>();

  private readonly connectListeners = new Set<() => void>();
  private readonly disconnectListeners = new Set<() => void>();
  private readonly permanentErrorListeners = new Set<(error: Error) => void>();

  constructor(options: WsBusClientOptions = {}) {
    this.url = options.url ?? defaultBusUrl();
    this.clientId = options.clientId ?? stableClientId();
    this.requestTimeoutS = options.requestTimeoutS ?? DEFAULT_REQUEST_TIMEOUT_S;
    this.logger = options.logger ?? SILENT_LOGGER;
    this.backoff = options.backoff ?? DEFAULT_BACKOFF;
    this.clock = options.clock ?? REAL_CLOCK;
    this.makeSocket = options.webSocketFactory ?? REAL_WEBSOCKET_FACTORY;
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
    listener?: BusEventListener,
  ): Promise<HydrateResult> {
    const normalized = normalizeProjectionTopics(
      typeof topics === 'string' ? [topics] : [...topics],
    );
    const id = `projection-${randomId()}`;
    const notificationId = `notification-${randomId()}`;
    let resolveInitial!: (result: HydrateResult) => void;
    let rejectInitial!: (error: Error) => void;
    const result = new Promise<HydrateResult>((resolve, reject) => {
      resolveInitial = resolve;
      rejectInitial = reject;
    });
    this.projections.set(id, {
      id,
      notificationId,
      topics: normalized,
      listener,
      cursor: null,
      notificationCursor: null,
      initialSettled: false,
      ready: false,
      tailBuffer: [],
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

  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe {
    const id = `terminal-${randomId()}`;
    const registration: TerminalRegistration = { id, sessionId, listener };
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

  detachTerminal(streamId: string): void {
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
      queueMicrotask(() => listener(error));
    }
    return () => this.permanentErrorListeners.delete(listener);
  }

  private async request(
    kind: 'query' | 'command',
    name: QueryName | CommandName,
    params: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const socket = await this.ensureConnected();
    const requestId = `request-${randomId()}`;
    const timeoutS = this.requestTimeoutS;
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRequests.delete(requestId);
        reject(new Error(`${kind} '${name}' timed out after ${timeoutS}s`));
      }, timeoutS * 1000);
      this.pendingRequests.set(requestId, {
        resolve,
        reject,
        cancelTimeout: () => clearTimeout(timer),
      });
      this.write(socket, {
        op: 'request',
        request_id: requestId,
        request: { kind, name, params } as
          | { kind: 'query'; name: QueryName; params: Record<string, unknown> }
          | { kind: 'command'; name: CommandName; params: Record<string, unknown> },
        timeout_s: timeoutS,
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
        for (const listener of [...this.connectListeners]) listener();
        await this.waitForClose();
        if ((this.state as ConnectionState) === 'closed') return;
        this.state = 'connecting';
        this.failAllRequests(new ConnectionLostError());
        for (const listener of [...this.disconnectListeners]) listener();
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
          for (const listener of [...this.permanentErrorListeners]) listener(normalized);
          return;
        }
        this.state = 'connecting';
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
          client: { client_id: this.clientId, kind: 'web' },
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
      case 'subscription.ready':
        this.acceptProjectionReady(message.subscription_id, message.snapshot);
        return;
      case 'subscription.event':
        this.acceptProjectionEvent(message.subscription_id, message.cursor, message.payload);
        return;
      case 'terminal.frame': {
        const stream = this.terminals.get(message.stream_id);
        if (stream !== undefined) {
          try {
            stream.listener(message.frame);
          } catch {
            // A terminal renderer cannot disrupt sibling streams or transport dispatch.
          }
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
    if (id === projection.notificationId) {
      projection.notificationCursor = Math.max(
        projection.notificationCursor ?? 0,
        snapshot.cursor,
      );
      for (const replay of snapshot.replay) {
        projection.notificationCursor = Math.max(
          projection.notificationCursor,
          replay.cursor,
        );
        this.notifyProjection(projection, replay.payload);
      }
      return;
    }
    projection.cursor = Math.max(projection.cursor ?? 0, snapshot.cursor);
    const replayItems: Array<{ seq: number; event: BusEvent }> = [];
    for (const item of snapshot.replay) {
      projection.cursor = Math.max(projection.cursor, item.cursor);
      if (isBusEvent(item.payload)) {
        replayItems.push({ seq: item.cursor, event: item.payload });
        this.notifyProjection(projection, item.payload);
      }
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
        replay: replayItems,
        unsubscribe: this.projectionDisposer(id),
      });
    }
  }

  private acceptProjectionEvent(
    id: string,
    cursor: number | null | undefined,
    payload: Record<string, unknown>,
  ): void {
    const projection = this.findProjection(id);
    if (projection === undefined) return;
    if (id !== projection.notificationId && !projection.ready) {
      projection.tailBuffer.push({ cursor: cursor ?? null, payload });
      return;
    }
    this.deliverProjectionPayload(projection, cursor, payload, id === projection.notificationId);
  }

  private deliverProjectionPayload(
    projection: ProjectionRegistration,
    cursor: number | null | undefined,
    payload: Record<string, unknown>,
    notification = false,
  ): void {
    if (cursor !== null && cursor !== undefined) {
      if (notification) {
        projection.notificationCursor = Math.max(projection.notificationCursor ?? 0, cursor);
      } else {
        projection.cursor = Math.max(projection.cursor ?? 0, cursor);
      }
    }
    this.notifyProjection(projection, payload);
  }

  private notifyProjection(
    projection: ProjectionRegistration,
    payload: Record<string, unknown>,
  ): void {
    if (!isBusEvent(payload)) return;
    try {
      projection.listener?.(payload);
    } catch {
      // Subscriber failures are isolated from transport dispatch.
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
        this.write(this.socket, {
          op: 'unsubscribe',
          subscription_id: projection.notificationId,
        });
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
      this.terminals.delete(message.stream_id);
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
    this.write(socket, {
      op: 'subscribe',
      subscription_id: projection.id,
      subscription: {
        kind: 'projections',
        topics: projection.topics,
        cursor: projection.cursor,
      },
    });
    this.write(socket, {
      op: 'subscribe',
      subscription_id: projection.notificationId,
      subscription: {
        kind: 'notifications',
        channels: ['errors'],
        cursor: projection.notificationCursor,
      },
    });
  }

  private findProjection(subscriptionId: string): ProjectionRegistration | undefined {
    const direct = this.projections.get(subscriptionId);
    if (direct !== undefined) return direct;
    for (const projection of this.projections.values()) {
      if (projection.notificationId === subscriptionId) return projection;
    }
    return undefined;
  }

  private sendTerminalAttach(socket: WebSocketLike, terminal: TerminalRegistration): void {
    this.write(socket, {
      op: 'terminal.attach',
      stream_id: terminal.id,
      target: { session_id: terminal.sessionId },
    });
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

function parseServerMessage(data: unknown): ServerMessage | undefined {
  if (typeof data !== 'string' || data.trim() === '') return undefined;
  try {
    const parsed = JSON.parse(data) as { op?: unknown };
    return isServerOp(parsed.op) ? (parsed as ServerMessage) : undefined;
  } catch {
    return undefined;
  }
}

function isServerOp(op: unknown): boolean {
  return (
    op === 'server.hello' ||
    op === 'reply' ||
    op === 'subscription.ready' ||
    op === 'subscription.event' ||
    op === 'terminal.attached' ||
    op === 'terminal.frame' ||
    op === 'error'
  );
}

function randomId(): string {
  try {
    return globalThis.crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

const ALL_PROJECTION_TOPICS: readonly ProjectionTopic[] = [
  'conversations',
  'roster',
  'schedule',
  'favorites',
  'templates',
  'themes',
  'workflows',
  'settings',
];

/** Transitional aliases are normalized at the client boundary and never sent on the public wire. */
function normalizeProjectionTopics(topics: readonly string[]): readonly ProjectionTopic[] {
  if (topics.length === 0 || topics.includes('all')) return ALL_PROJECTION_TOPICS;
  const aliases: Readonly<Record<string, readonly ProjectionTopic[]>> = {
    crow: ['roster'],
    crows: ['roster'],
    tickets: ['schedule'],
    prefs: ['favorites', 'templates', 'themes', 'workflows', 'settings'],
    preferences: ['favorites', 'templates', 'themes', 'workflows', 'settings'],
  };
  const valid = new Set<string>(ALL_PROJECTION_TOPICS);
  const out = new Set<ProjectionTopic>();
  for (const topic of topics) {
    const mapped = aliases[topic];
    if (mapped !== undefined) {
      for (const value of mapped) out.add(value);
    } else if (valid.has(topic)) {
      out.add(topic as ProjectionTopic);
    } else {
      throw new Error(`unsupported projection topic '${topic}'`);
    }
  }
  return [...out];
}

export type { ProjectionTopic };
