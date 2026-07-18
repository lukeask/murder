/** Unix-socket JSON-lines transport for the generated application protocol. */

import type { Buffer } from 'node:buffer';
import { randomUUID } from 'node:crypto';
import { connect as netConnect, type Socket } from 'node:net';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ClientHello,
  type ClientKind,
  type ClientMessage,
  type CommandName,
  type ErrorMessage,
  type ProjectionTopic,
  type QueryName,
  type RequestMessage,
  type ServerMessage,
  type SubscribeMessage,
  type TerminalAttachMessage,
  type TerminalChunk,
  type TerminalDetachMessage,
  type TerminalFrame,
  type UnsubscribeMessage,
} from '../generated/applicationProtocol.js';
import type {
  BusClient,
  BusEventListener,
  CommandMethod,
  CommandParams,
  CommandResult,
  HydrateReply,
  HydrateResult,
  ProjectionInvalidation,
  ProjectionInvalidationListener,
  ProjectionTopics,
  QueryMethod,
  QueryParams,
  QueryResult,
  TerminalFrameListener,
  Unsubscribe,
} from './BusClient.js';
import { isBusEvent } from './matchesFilter.js';
import { unwrapReadReply } from './readEnvelope.js';

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
        if (timer !== undefined) {
          clearTimeout(timer);
        }
        resolveSleep?.();
      },
    };
  },
  random: Math.random,
};

export interface UdsBusClientOptions {
  socketPath: string;
  clientKind?: ClientKind;
  clientId?: string;
  /** Retained option spelling for callers; application request timeout in seconds. */
  rpcTimeoutS?: number;
  backoff?: BackoffConfig;
  clock?: Clock;
  logger?: BusLogger;
}

export class ProtocolVersionMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolVersionMismatchError';
  }
}

/** Retained class name for compatibility; requests are no longer legacy RPC wire messages. */
export class RpcTimeoutError extends Error {
  constructor(name: string, timeoutS: number) {
    super(`request '${name}' timed out after ${timeoutS}s`);
    this.name = 'RpcTimeoutError';
  }
}

export class ConnectionLostError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConnectionLostError';
  }
}

export class LineBuffer {
  private buffer = '';

  push(chunk: string): string[] {
    this.buffer += chunk;
    const lines: string[] = [];
    let index = this.buffer.indexOf('\n');
    while (index >= 0) {
      lines.push(this.buffer.slice(0, index));
      this.buffer = this.buffer.slice(index + 1);
      index = this.buffer.indexOf('\n');
    }
    return lines;
  }
}

interface PendingRequest {
  resolve(result: Record<string, unknown>): void;
  reject(error: Error): void;
  cancelTimeout(): void;
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(error: Error): void;
}

interface ProjectionHydration {
  readonly topics: readonly ProjectionTopic[];
  readonly listener: BusEventListener | undefined;
  readonly invalidationListener: ProjectionInvalidationListener | undefined;
  readonly subscriptionId: string;
  readonly notificationId: string;
  readonly initial: Deferred<HydrateReply>;
  initialSettled: boolean;
  ready: boolean;
  projectionCursor: number | undefined;
  notificationCursor: number | undefined;
  tailBuffer: Array<{ cursor: number | null; payload: Record<string, unknown> }>;
}

interface TerminalAttachment {
  readonly sessionId: string | null;
  readonly listener: TerminalFrameListener;
  readonly streamId: string;
  lastSequence: number;
  resyncPending: boolean;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'closed';

export class UdsBusClient implements BusClient {
  private readonly socketPath: string;
  private readonly clientKind: ClientKind;
  private readonly clientId: string;
  private readonly requestTimeoutS: number;
  private readonly backoff: BackoffConfig;
  private readonly clock: Clock;
  private readonly logger: BusLogger;

  private state: ConnectionState = 'idle';
  private socket: Socket | undefined;
  private lineBuffer = new LineBuffer();
  private loop: Promise<void> | undefined;
  private pendingSleep: { cancel(): void } | undefined;
  private factCursor: number | undefined;
  private projectionCursor: number | undefined;
  private readonly pendingRequests = new Map<string, PendingRequest>();
  private readonly hydrations = new Map<string, ProjectionHydration>();
  private readonly notificationHydrations = new Map<string, ProjectionHydration>();
  private readonly terminals = new Map<string, TerminalAttachment>();
  private readonly connectWaiters = new Set<{
    resolve: () => void;
    reject: (error: Error) => void;
  }>();
  private readonly connectListeners = new Set<() => void>();
  private readonly disconnectListeners = new Set<() => void>();
  private readonly permanentErrorListeners = new Set<(error: Error) => void>();

  constructor(options: UdsBusClientOptions) {
    this.socketPath = options.socketPath;
    this.clientKind = options.clientKind ?? 'tui';
    this.clientId = options.clientId ?? `${this.clientKind}-${randomUUID()}`;
    this.requestTimeoutS = options.rpcTimeoutS ?? DEFAULT_REQUEST_TIMEOUT_S;
    this.backoff = options.backoff ?? DEFAULT_BACKOFF;
    this.clock = options.clock ?? REAL_CLOCK;
    this.logger = options.logger ?? SILENT_LOGGER;
  }

  connect(): Promise<void> {
    if (this.state === 'connected') {
      return Promise.resolve();
    }
    if (this.state === 'closed') {
      return Promise.reject(new ConnectionLostError('client is closed'));
    }
    if (this.loop === undefined) {
      this.loop = this.runConnectLoop().finally(() => {
        this.loop = undefined;
      });
    }
    return new Promise<void>((resolve, reject) => {
      this.connectWaiters.add({ resolve, reject });
    });
  }

  getFactCursor(): number | undefined {
    return this.factCursor;
  }

  getProjectionCursor(): number | undefined {
    return this.projectionCursor;
  }

  async query<M extends QueryMethod>(name: M, params: QueryParams<M>): Promise<QueryResult<M>> {
    const result = await this.request('query', name, params);
    return unwrapReadReply(name, result) as QueryResult<M>;
  }

  async command<M extends CommandMethod>(
    name: M,
    params: CommandParams<M>,
  ): Promise<CommandResult<M>> {
    const result = await this.request('command', name, params);
    return result as CommandResult<M>;
  }

  async hydrate(
    topics: ProjectionTopics,
    listener?: BusEventListener,
    invalidationListener?: ProjectionInvalidationListener,
    since?: number | null,
  ): Promise<HydrateResult> {
    const hydration: ProjectionHydration = {
      topics: normalizeProjectionTopics(topics),
      listener,
      invalidationListener,
      subscriptionId: `projection-${randomUUID()}`,
      notificationId: `notifications-${randomUUID()}`,
      initial: createDeferred<HydrateReply>(),
      initialSettled: false,
      ready: false,
      projectionCursor: resolveInitialProjectionCursor(since, this.projectionCursor),
      notificationCursor: undefined,
      tailBuffer: [],
    };
    this.hydrations.set(hydration.subscriptionId, hydration);
    this.notificationHydrations.set(hydration.notificationId, hydration);
    const unsubscribe = this.hydrationDisposer(hydration);
    try {
      if (this.state === 'connected' && this.socket !== undefined) {
        this.sendHydration(this.socket, hydration);
      } else {
        // Handshake resend (like WS resendStreams) covers registrations already in the map.
        await this.ensureConnected();
      }
      const reply = await hydration.initial.promise;
      return { ...reply, unsubscribe };
    } catch (error) {
      this.hydrations.delete(hydration.subscriptionId);
      this.notificationHydrations.delete(hydration.notificationId);
      throw error;
    }
  }

  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe {
    const attachment: TerminalAttachment = {
      sessionId,
      listener,
      streamId: `terminal-${randomUUID()}`,
      lastSequence: 0,
      resyncPending: false,
    };
    this.terminals.set(attachment.streamId, attachment);
    if (this.state === 'connected' && this.socket !== undefined) {
      this.sendTerminalAttach(this.socket, attachment);
    } else {
      void this.connect().catch(() => {});
    }
    let disposed = false;
    return () => {
      if (disposed) {
        return;
      }
      disposed = true;
      this.terminals.delete(attachment.streamId);
      if (this.state === 'connected' && this.socket !== undefined) {
        const message: TerminalDetachMessage = {
          op: 'terminal.detach',
          stream_id: attachment.streamId,
        };
        this.writeMessage(this.socket, message);
      }
    };
  }

  onConnect(listener: () => void): Unsubscribe {
    this.connectListeners.add(listener);
    if (this.state === 'connected') {
      listener();
    }
    return () => this.connectListeners.delete(listener);
  }

  onDisconnect(listener: () => void): Unsubscribe {
    this.disconnectListeners.add(listener);
    return () => this.disconnectListeners.delete(listener);
  }

  onPermanentError(listener: (error: Error) => void): Unsubscribe {
    this.permanentErrorListeners.add(listener);
    return () => this.permanentErrorListeners.delete(listener);
  }

  close(): void {
    if (this.state === 'closed') {
      return;
    }
    this.state = 'closed';
    this.pendingSleep?.cancel();
    const error = new ConnectionLostError('client closed');
    this.failPendingRequests(error);
    this.rejectConnectWaiters(error);
    for (const hydration of this.hydrations.values()) {
      if (!hydration.initialSettled) {
        hydration.initial.reject(error);
      }
    }
    this.hydrations.clear();
    this.notificationHydrations.clear();
    this.terminals.clear();
    this.connectListeners.clear();
    this.disconnectListeners.clear();
    this.permanentErrorListeners.clear();
    this.teardownSocket();
  }

  private async request(
    kind: 'query' | 'command',
    name: string,
    params: unknown,
  ): Promise<Record<string, unknown>> {
    const socket = await this.ensureConnected();
    const requestId = `request-${randomUUID()}`;
    const timeoutS = this.requestTimeoutS;
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(
        () => {
          this.pendingRequests.delete(requestId);
          reject(new RpcTimeoutError(name, timeoutS));
        },
        (timeoutS + 1) * 1000,
      );
      this.pendingRequests.set(requestId, {
        resolve,
        reject,
        cancelTimeout: () => clearTimeout(timer),
      });
      const request =
        kind === 'query'
          ? {
              kind: 'query' as const,
              name: name as QueryName,
              params: params as Record<string, unknown>,
            }
          : {
              kind: 'command' as const,
              name: name as CommandName,
              params: params as Record<string, unknown>,
            };
      const message: RequestMessage = {
        op: 'request',
        request_id: requestId,
        request,
        timeout_s: timeoutS,
      };
      this.writeMessage(socket, message);
    });
  }

  private hydrationDisposer(hydration: ProjectionHydration): Unsubscribe {
    let disposed = false;
    return () => {
      if (disposed) {
        return;
      }
      disposed = true;
      this.hydrations.delete(hydration.subscriptionId);
      this.notificationHydrations.delete(hydration.notificationId);
      if (!hydration.initialSettled) {
        hydration.initial.reject(new ConnectionLostError('hydrate unsubscribed'));
      }
      if (this.state === 'connected' && this.socket !== undefined) {
        const message: UnsubscribeMessage = {
          op: 'unsubscribe',
          subscription_id: hydration.subscriptionId,
        };
        this.writeMessage(this.socket, message);
        this.writeMessage(this.socket, {
          op: 'unsubscribe',
          subscription_id: hydration.notificationId,
        });
      }
    };
  }

  private async runConnectLoop(): Promise<void> {
    let attempt = 0;
    while (!this.isClosed()) {
      let established = false;
      try {
        const trailing = await this.openAndHandshake();
        attempt = 0;
        established = true;
        this.state = 'connected';
        const socket = this.socket;
        if (socket === undefined) {
          throw new ConnectionLostError('no socket after handshake');
        }
        this.resolveConnectWaiters();
        for (const hydration of this.hydrations.values()) {
          this.sendHydration(socket, hydration);
        }
        for (const attachment of this.terminals.values()) {
          this.sendTerminalAttach(socket, attachment);
        }
        for (const line of trailing) {
          const message = this.parseServerMessage(line);
          if (message !== undefined) {
            this.dispatch(message);
          }
        }
        this.notify(this.connectListeners, undefined);
        await this.readUntilClosed();
        if (this.isClosed()) {
          return;
        }
        this.state = 'connecting';
        this.teardownSocket();
        this.failPendingRequests(new ConnectionLostError('connection dropped'));
        for (const hydration of this.hydrations.values()) {
          hydration.ready = false;
          hydration.tailBuffer = [];
        }
        this.notify(this.disconnectListeners, undefined);
      } catch (error) {
        this.teardownSocket();
        if (error instanceof ProtocolVersionMismatchError) {
          this.state = 'closed';
          this.failPendingRequests(error);
          this.rejectConnectWaiters(error);
          this.notify(this.permanentErrorListeners, error);
          return;
        }
        this.state = 'connecting';
        this.logger.warn(`application connection error: ${stringifyError(error)}`);
        if (established) {
          this.failPendingRequests(new ConnectionLostError('connection dropped'));
          for (const hydration of this.hydrations.values()) {
            hydration.ready = false;
            hydration.tailBuffer = [];
          }
          this.notify(this.disconnectListeners, undefined);
        }
      }

      if (this.isClosed()) {
        return;
      }
      const delay = this.nextBackoffMs(attempt++);
      const sleeper = this.clock.sleep(delay);
      this.pendingSleep = sleeper;
      await sleeper.promise;
      this.pendingSleep = undefined;
    }
  }

  private async openAndHandshake(): Promise<string[]> {
    this.state = 'connecting';
    this.teardownSocket();
    this.lineBuffer = new LineBuffer();
    const socket = await this.openSocket();
    this.socket = socket;
    const hello: ClientHello = {
      op: 'client.hello',
      protocol_version: APPLICATION_PROTOCOL_VERSION,
      client: { client_id: this.clientId, kind: this.clientKind },
    };
    return this.handshakeExchange(socket, hello);
  }

  private handshakeExchange(socket: Socket, hello: ClientHello): Promise<string[]> {
    return new Promise<string[]>((resolve, reject) => {
      let settled = false;
      const settle = (action: () => void): void => {
        if (settled) return;
        settled = true;
        socket.removeListener('data', onData);
        socket.removeListener('error', onError);
        socket.removeListener('close', onClose);
        action();
      };
      const onData = (chunk: Buffer): void => {
        const lines = this.lineBuffer.push(chunk.toString('utf8'));
        for (let index = 0; index < lines.length; index += 1) {
          const line = lines[index];
          if (line === undefined) continue;
          const message = this.parseServerMessage(line);
          if (message?.op === 'server.hello') {
            if (message.protocol_version !== APPLICATION_PROTOCOL_VERSION) {
              settle(() =>
                reject(
                  new ProtocolVersionMismatchError(
                    `server=${message.protocol_version} client=${APPLICATION_PROTOCOL_VERSION}`,
                  ),
                ),
              );
            } else {
              this.factCursor =
                typeof message.fact_cursor === 'number' ? message.fact_cursor : 0;
              this.projectionCursor =
                typeof message.projection_cursor === 'number' ? message.projection_cursor : 0;
              settle(() => resolve(lines.slice(index + 1)));
            }
            return;
          }
          if (message?.op === 'error') {
            settle(() =>
              reject(
                message.error.code === 'version_mismatch'
                  ? new ProtocolVersionMismatchError(message.error.message)
                  : new ConnectionLostError(`handshake rejected: ${message.error.message}`),
              ),
            );
            return;
          }
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

  private readUntilClosed(): Promise<void> {
    const socket = this.socket;
    if (socket === undefined) {
      return Promise.reject(new ConnectionLostError('no socket to read'));
    }
    return new Promise<void>((resolve) => {
      const onData = (chunk: Buffer): void => {
        for (const line of this.lineBuffer.push(chunk.toString('utf8'))) {
          const message = this.parseServerMessage(line);
          if (message !== undefined) this.dispatch(message);
        }
      };
      const onClose = (): void => {
        socket.removeListener('data', onData);
        socket.removeListener('close', onClose);
        socket.removeListener('error', onError);
        resolve();
      };
      const onError = (error: Error): void =>
        this.logger.warn(`application socket error: ${error.message}`);
      socket.on('data', onData);
      socket.on('close', onClose);
      socket.on('error', onError);
    });
  }

  private dispatch(message: ServerMessage): void {
    switch (message.op) {
      case 'reply':
        this.settleRequest(message.request_id, (request) => request.resolve(message.result));
        return;
      case 'subscription.ready':
        if (this.notificationHydrations.has(message.subscription_id)) {
          const hydration = this.notificationHydrations.get(message.subscription_id);
          if (hydration !== undefined) {
            hydration.notificationCursor = advanceCursor(
              hydration.notificationCursor,
              message.snapshot.cursor,
            );
          }
          for (const item of message.snapshot.replay) {
            this.deliverNotificationEvent(message.subscription_id, item.cursor, item.payload);
          }
          return;
        }
        this.settleHydration(message.subscription_id, message.snapshot);
        return;
      case 'subscription.event':
        if (this.notificationHydrations.has(message.subscription_id)) {
          this.deliverNotificationEvent(
            message.subscription_id,
            message.cursor ?? null,
            message.payload,
          );
          return;
        }
        this.deliverProjectionEvent(
          message.subscription_id,
          message.cursor ?? null,
          message.payload,
        );
        return;
      case 'terminal.frame': {
        const attachment = this.terminals.get(message.stream_id);
        if (attachment !== undefined) {
          this.acceptTerminalFrame(attachment, message.frame);
        }
        return;
      }
      case 'terminal.chunk': {
        const attachment = this.terminals.get(message.stream_id);
        if (attachment !== undefined) {
          this.acceptTerminalChunk(attachment, message.chunk);
        }
        return;
      }
      case 'terminal.gap': {
        const attachment = this.terminals.get(message.stream_id);
        if (attachment !== undefined) {
          this.requestTerminalResync(attachment, 'gap');
        }
        return;
      }
      case 'terminal.resynced': {
        const attachment = this.terminals.get(message.stream_id);
        if (attachment !== undefined) {
          attachment.resyncPending = false;
          this.acceptTerminalFrame(attachment, message.frame);
        }
        return;
      }
      case 'error':
        this.dispatchError(message);
        return;
      case 'server.hello':
      case 'terminal.attached':
        return;
      default:
        assertNever(message);
    }
  }

  private settleHydration(
    subscriptionId: string,
    snapshot: Extract<ServerMessage, { op: 'subscription.ready' }>['snapshot'],
  ): void {
    const hydration = this.hydrations.get(subscriptionId);
    if (hydration === undefined) return;
    hydration.projectionCursor = advanceCursor(hydration.projectionCursor, snapshot.cursor);
    const replay: Array<{ seq: number; event: Parameters<BusEventListener>[0] }> = [];
    for (const item of snapshot.replay) {
      hydration.projectionCursor = advanceCursor(hydration.projectionCursor, item.cursor);
      if (isProjectionInvalidation(item.payload)) {
        this.callProjectionInvalidationListener(hydration.invalidationListener, item.payload);
      } else if (!hasProjectionInvalidationType(item.payload) && isBusEvent(item.payload)) {
        replay.push({ seq: item.cursor, event: item.payload });
        this.callBusListener(hydration.listener, item.payload);
      }
    }
    hydration.ready = true;
    for (const item of hydration.tailBuffer) {
      this.deliverProjectionEvent(subscriptionId, item.cursor, item.payload);
    }
    hydration.tailBuffer = [];
    if (!hydration.initialSettled) {
      hydration.initialSettled = true;
      hydration.initial.resolve({
        snapshots: snapshot.snapshots,
        cursor: snapshot.cursor,
        mode: snapshot.mode,
        replay,
      });
    }
  }

  private deliverProjectionEvent(
    subscriptionId: string,
    cursor: number | null,
    payload: Record<string, unknown>,
  ): void {
    const hydration = this.hydrations.get(subscriptionId);
    if (hydration === undefined) return;
    if (!hydration.ready) {
      hydration.tailBuffer.push({ cursor, payload });
      return;
    }
    hydration.projectionCursor = advanceCursor(hydration.projectionCursor, cursor);
    if (isProjectionInvalidation(payload)) {
      this.callProjectionInvalidationListener(hydration.invalidationListener, payload);
    } else if (!hasProjectionInvalidationType(payload) && isBusEvent(payload)) {
      this.callBusListener(hydration.listener, payload);
    }
  }

  private deliverNotificationEvent(
    subscriptionId: string,
    cursor: number | null,
    payload: Record<string, unknown>,
  ): void {
    const hydration = this.notificationHydrations.get(subscriptionId);
    if (hydration === undefined) return;
    hydration.notificationCursor = advanceCursor(hydration.notificationCursor, cursor);
    if (isBusEvent(payload)) {
      this.callBusListener(hydration.listener, payload);
    }
  }

  private dispatchError(message: ErrorMessage): void {
    const error = new Error(`application error [${message.error.code}]: ${message.error.message}`);
    if (message.request_id !== undefined && message.request_id !== null) {
      this.settleRequest(message.request_id, (request) => request.reject(error));
      return;
    }
    if (message.subscription_id !== undefined && message.subscription_id !== null) {
      const hydration =
        this.hydrations.get(message.subscription_id) ??
        this.notificationHydrations.get(message.subscription_id);
      if (hydration !== undefined && !hydration.initialSettled) {
        hydration.initialSettled = true;
        hydration.initial.reject(error);
        this.hydrations.delete(hydration.subscriptionId);
        this.notificationHydrations.delete(hydration.notificationId);
      }
      return;
    }
    if (message.stream_id !== undefined && message.stream_id !== null) {
      this.logger.warn(error.message);
      return;
    }
    this.logger.warn(error.message);
  }

  private sendHydration(socket: Socket, hydration: ProjectionHydration): void {
    hydration.ready = false;
    const message: SubscribeMessage = {
      op: 'subscribe',
      subscription_id: hydration.subscriptionId,
      subscription: {
        kind: 'projections',
        topics: hydration.topics,
        ...(hydration.projectionCursor !== undefined ? { cursor: hydration.projectionCursor } : {}),
      },
    };
    this.writeMessage(socket, message);
    this.writeMessage(socket, {
      op: 'subscribe',
      subscription_id: hydration.notificationId,
      subscription: {
        kind: 'notifications',
        channels: ['errors'],
        ...(hydration.notificationCursor !== undefined
          ? { cursor: hydration.notificationCursor }
          : {}),
      },
    });
  }

  private sendTerminalAttach(socket: Socket, attachment: TerminalAttachment): void {
    const message: TerminalAttachMessage = {
      op: 'terminal.attach',
      stream_id: attachment.streamId,
      target:
        attachment.sessionId === null
          ? { session_id: null }
          : UUID_RE.test(attachment.sessionId)
            ? { session_id: attachment.sessionId }
            : { legacy_agent_id: attachment.sessionId },
      after_sequence: attachment.lastSequence,
    };
    this.writeMessage(socket, message);
  }

  private acceptTerminalFrame(attachment: TerminalAttachment, frame: TerminalFrame): void {
    if (frame.sequence <= attachment.lastSequence) return;
    if (!frame.reset) {
      this.requestTerminalResync(attachment, 'unsupported_mode');
      return;
    }
    // A reset frame recovers sequence jumps from latest-frame-wins coalescing.
    attachment.lastSequence = frame.sequence;
    attachment.resyncPending = false;
    this.callTerminalListener(attachment, frame);
  }

  private acceptTerminalChunk(attachment: TerminalAttachment, chunk: TerminalChunk): void {
    const expected = attachment.lastSequence + 1;
    if (attachment.lastSequence === 0 || chunk.sequence !== expected) {
      this.requestTerminalResync(attachment, 'gap');
      return;
    }
    attachment.lastSequence = chunk.sequence;
    this.callTerminalListener(attachment, chunk);
  }

  private requestTerminalResync(
    attachment: TerminalAttachment,
    reason: 'gap' | 'unsupported_mode',
  ): void {
    if (attachment.resyncPending) return;
    const socket = this.socket;
    if (this.state !== 'connected' || socket === undefined) return;
    attachment.resyncPending = true;
    this.writeMessage(socket, {
      op: 'terminal.resync',
      stream_id: attachment.streamId,
      after_sequence: attachment.lastSequence,
      reason,
    });
  }

  private callTerminalListener(
    attachment: TerminalAttachment,
    update: TerminalFrame | TerminalChunk,
  ): void {
    try {
      attachment.listener(update);
    } catch {
      // One presentation listener must not tear down the socket read loop.
    }
  }

  private settleRequest(id: string, settle: (request: PendingRequest) => void): void {
    const request = this.pendingRequests.get(id);
    if (request === undefined) return;
    this.pendingRequests.delete(id);
    request.cancelTimeout();
    settle(request);
  }

  private resolveConnectWaiters(): void {
    for (const waiter of this.connectWaiters) {
      waiter.resolve();
    }
    this.connectWaiters.clear();
  }

  private rejectConnectWaiters(error: Error): void {
    for (const waiter of this.connectWaiters) {
      waiter.reject(error);
    }
    this.connectWaiters.clear();
  }

  private failPendingRequests(error: Error): void {
    for (const request of this.pendingRequests.values()) {
      request.cancelTimeout();
      request.reject(error);
    }
    this.pendingRequests.clear();
  }

  private callBusListener(
    listener: BusEventListener | undefined,
    event: Parameters<BusEventListener>[0],
  ): void {
    if (listener === undefined) return;
    try {
      listener(event);
    } catch {
      // A store listener owns its own error state.
    }
  }

  private callProjectionInvalidationListener(
    listener: ProjectionInvalidationListener | undefined,
    invalidation: ProjectionInvalidation,
  ): void {
    if (listener === undefined) return;
    try {
      listener(invalidation);
    } catch {
      // A projection consumer owns its own error state.
    }
  }

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
        socket.removeListener('error', onError);
        resolve(socket);
      };
      const onError = (error: Error): void => {
        socket.removeListener('connect', onConnect);
        reject(new ConnectionLostError(`connect failed: ${error.message}`));
      };
      socket.once('connect', onConnect);
      socket.once('error', onError);
    });
  }

  private writeMessage(socket: Socket, message: ClientMessage): void {
    socket.write(`${JSON.stringify(message)}\n`);
  }

  private parseServerMessage(line: string): ServerMessage | undefined {
    if (line.trim() === '') return undefined;
    try {
      const value: unknown = JSON.parse(line);
      if (!isRecord(value) || typeof value['op'] !== 'string') {
        this.logger.warn('ignoring malformed application message');
        return undefined;
      }
      return value as unknown as ServerMessage;
    } catch (error) {
      this.logger.warn(`ignoring invalid application JSON: ${stringifyError(error)}`);
      return undefined;
    }
  }

  private nextBackoffMs(attempt: number): number {
    const ceiling = Math.min(this.backoff.capMs, this.backoff.baseMs * 2 ** attempt);
    return this.clock.random() * ceiling;
  }

  private teardownSocket(): void {
    const socket = this.socket;
    this.socket = undefined;
    if (socket !== undefined) {
      socket.removeAllListeners();
      socket.destroy();
    }
  }

  private isClosed(): boolean {
    return this.state === 'closed';
  }

  private notify<T>(listeners: ReadonlySet<(value: T) => void>, value: T): void {
    for (const listener of [...listeners]) {
      try {
        listener(value);
      } catch {
        // Lifecycle observers cannot own the transport loop.
      }
    }
  }
}

function normalizeProjectionTopics(topics: ProjectionTopics): readonly ProjectionTopic[] {
  const list: readonly string[] = typeof topics === 'string' ? [topics] : [...topics];
  if (list.length === 0 || list.includes('all')) return ALL_PROJECTION_TOPICS;
  const aliases: Readonly<Record<string, readonly ProjectionTopic[]>> = {
    crow: ['roster'],
    crows: ['roster'],
    tickets: ['schedule'],
    prefs: ['favorites', 'templates', 'themes', 'workflows', 'settings'],
    preferences: ['favorites', 'templates', 'themes', 'workflows', 'settings'],
  };
  const valid = new Set<string>(ALL_PROJECTION_TOPICS);
  const out = new Set<ProjectionTopic>();
  for (const topic of list) {
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

/**
 * Resolve the initial projection subscribe cursor.
 * - `null` → cold (omit cursor)
 * - number → explicit since
 * - omitted → default to the ServerHello projection watermark when known
 */
function resolveInitialProjectionCursor(
  since: number | null | undefined,
  helloProjectionCursor: number | undefined,
): number | undefined {
  if (since === null) return undefined;
  if (typeof since === 'number') return since;
  return helloProjectionCursor;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const ALL_PROJECTION_TOPICS: readonly ProjectionTopic[] = [
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
];

const PROJECTION_TOPICS: ReadonlySet<string> = new Set(ALL_PROJECTION_TOPICS);

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
    (source_fact_id === null ||
      (typeof source_fact_id === 'string' && UUID_RE.test(source_fact_id)))
  );
}

function hasProjectionInvalidationType(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const { type } = value;
  return type === 'projection.invalidate';
}

function advanceCursor(
  current: number | undefined,
  next: number | null | undefined,
): number | undefined {
  if (typeof next !== 'number') return current;
  return current === undefined ? next : Math.max(current, next);
}

function stringifyError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function assertNever(value: never): never {
  throw new Error(`unhandled application message: ${JSON.stringify(value)}`);
}
