/** Direct WebSocket implementation of Murder's closed application protocol. */

import { randomUUID } from 'node:crypto';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ApplicationRequest,
  type ClientMessage,
  type CommandName,
  type PlanSeedFailedNotification,
  type ProjectionTopic,
  type QueryName,
  type ServerMessage,
  type TerminalFrame,
} from '../generated/applicationProtocol.js';
import type {
  ApplicationClient,
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
  TerminalAttachMode,
  TerminalFrameListener,
  TerminalInputBatch,
  TerminalInputLease,
  Unsubscribe,
} from './ApplicationClient.js';
import { unwrapReadReply } from './normalizeReply.js';

export interface WebSocketLike {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((event: unknown) => void) | null;
  onclose: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
}

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

export type WebSocketFactory = (url: string) => WebSocketLike;

export interface ApplicationWebSocketClientOptions {
  url?: string;
  clientId?: string;
  kind?: 'tui' | 'web';
  clock?: Clock;
  webSocketFactory?: WebSocketFactory;
}

export class ConnectionLostError extends Error {
  constructor(message = 'application WebSocket connection lost') {
    super(message);
    this.name = 'ConnectionLostError';
  }
}

interface Pending {
  resolve(value: Record<string, unknown>): void;
  reject(reason: Error): void;
  timer: ReturnType<typeof setTimeout>;
}

interface ProjectionSubscription {
  id: string;
  topics: ProjectionTopic[];
  cursor: number | undefined;
  invalidation: ProjectionInvalidationListener | undefined;
  snapshotListener: ((reply: HydrateReply) => void) | undefined;
  ready: boolean;
  subscribedGeneration: number;
  resolve(value: HydrateReply): void;
  reject(reason: Error): void;
}

interface TerminalSubscription {
  id: string;
  sessionId: string | null;
  listener: TerminalFrameListener;
  sequence: number;
  suspended: boolean;
  resyncPending: boolean;
  mode: TerminalAttachMode;
  attachedGeneration: number;
}

export class ApplicationWebSocketClient implements ApplicationClient {
  private socket: WebSocketLike | undefined;
  private connecting: Promise<void> | undefined;
  private closed = false;
  private readonly pending = new Map<string, Pending>();
  private readonly projections = new Map<string, ProjectionSubscription>();
  private readonly terminals = new Map<string, TerminalSubscription>();
  private readonly terminalInputWatchers = new Map<string, Set<(error: Error) => void>>();
  private readonly connected = new Set<() => void>();
  private readonly disconnected = new Set<() => void>();
  private readonly planSeedFailed = new Set<(notification: PlanSeedFailedNotification) => void>();
  private projectionCursor: number | undefined;
  private factCursor: number | undefined;
  private serverId: string | undefined;
  private connectionGeneration = 0;
  private reconnectCancel: (() => void) | undefined;
  private readonly url: string;
  private readonly clientId: string;
  private readonly kind: 'tui' | 'web';
  private readonly clock: Clock;
  private readonly makeSocket: WebSocketFactory | undefined;

  constructor(options: string | ApplicationWebSocketClientOptions = {}, clientId?: string) {
    const normalized: ApplicationWebSocketClientOptions =
      typeof options === 'string'
        ? { url: options, ...(clientId === undefined ? {} : { clientId }) }
        : options;
    this.url = normalized.url ?? 'ws://localhost/api/ws';
    this.clientId = normalized.clientId ?? `tui-${randomUUID()}`;
    this.kind = normalized.kind ?? 'tui';
    this.clock = normalized.clock ?? REAL_CLOCK;
    this.makeSocket = normalized.webSocketFactory;
  }

  async connect(): Promise<void> {
    if (this.closed) throw new ConnectionLostError('application client is closed');
    if (this.socket?.readyState === 1) return;
    if (this.connecting === undefined) this.connecting = this.open();
    return this.connecting;
  }

  getFactCursor(): number | undefined {
    return this.factCursor;
  }
  getProjectionCursor(): number | undefined {
    return this.projectionCursor;
  }
  onConnect(listener: () => void): Unsubscribe {
    this.connected.add(listener);
    return () => this.connected.delete(listener);
  }
  onDisconnect(listener: () => void): Unsubscribe {
    this.disconnected.add(listener);
    return () => this.disconnected.delete(listener);
  }
  onPermanentError(_listener: (error: Error) => void): Unsubscribe {
    return () => {};
  }
  onPlanSeedFailed(listener: (notification: PlanSeedFailedNotification) => void): Unsubscribe {
    this.planSeedFailed.add(listener);
    return () => this.planSeedFailed.delete(listener);
  }

  async query<M extends QueryMethod>(name: M, params: QueryParams<M>): Promise<QueryResult<M>> {
    return unwrapReadReply(name, await this.request('query', name, params)) as QueryResult<M>;
  }

  async command<M extends CommandMethod>(
    name: M,
    params: CommandParams<M>,
  ): Promise<CommandResult<M>> {
    return (await this.request('command', name, params)) as CommandResult<M>;
  }

  async hydrate(
    topics: ProjectionTopics,
    invalidation?: ProjectionInvalidationListener,
    since?: number | null,
    snapshotListener?: (reply: HydrateReply) => void,
  ): Promise<HydrateResult> {
    const id = `projection-${randomUUID()}`;
    const selected = (Array.isArray(topics) ? topics : [topics]) as ProjectionTopic[];
    const reply = new Promise<HydrateReply>((resolve, reject) => {
      this.projections.set(id, {
        id,
        topics: selected,
        cursor: since === null ? undefined : (since ?? this.projectionCursor),
        invalidation,
        snapshotListener,
        ready: false,
        subscribedGeneration: 0,
        resolve,
        reject,
      });
    });
    try {
      await this.connect();
    } catch (error) {
      const subscription = this.projections.get(id);
      if (subscription !== undefined) {
        this.projections.delete(id);
        subscription.reject(
          error instanceof Error ? error : new ConnectionLostError('application connection failed'),
        );
      }
      return { ...(await reply), unsubscribe: () => this.unsubscribe(id) };
    }
    const subscription = this.projections.get(id);
    if (subscription !== undefined) this.reconcileSubscription(subscription);
    return { ...(await reply), unsubscribe: () => this.unsubscribe(id) };
  }

  attachTerminal(
    sessionId: string | null,
    listener: TerminalFrameListener,
    mode: TerminalAttachMode = 'raw',
  ): Unsubscribe {
    const id = `terminal-${randomUUID()}`;
    const terminal = {
      id,
      sessionId,
      listener,
      sequence: 0,
      suspended: false,
      resyncPending: false,
      mode,
      attachedGeneration: 0,
    };
    this.terminals.set(id, terminal);
    if (this.socket?.readyState === 1) {
      this.reconcileTerminal(terminal);
    } else {
      void this.connect()
        .then(() => {
          if (this.terminals.get(id) === terminal) this.reconcileTerminal(terminal);
        })
        .catch(() => {});
    }
    return () => {
      this.terminals.delete(id);
      this.send({ op: 'terminal.detach', stream_id: id });
    };
  }

  async openTerminalInput(sessionId: string): Promise<TerminalInputLease> {
    const result = await this.command('session.writer.acquire', {
      session_id: sessionId,
      mode: 'raw_terminal',
      holder: { kind: 'client', id: this.clientId },
    });
    if (!('lease' in result)) {
      throw new Error(`terminal input unavailable: ${result.reason}`);
    }
    return {
      streamId: `terminal-input-${randomUUID()}`,
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
    if (!('lease' in result)) {
      throw new Error(`terminal input unavailable: ${result.reason}`);
    }
    return {
      ...lease,
      leaseId: result.lease.lease_id,
      fence: result.lease.fence,
    };
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
      this.send({ op: 'terminal.input_detach', stream_id: lease.streamId });
    }
  }

  sendTerminalInput(batch: TerminalInputBatch): boolean {
    if (this.socket?.readyState !== 1) return false;
    // The generated ClientMessage union gains this member with the protocol generation. Keep the
    // transport boundary structural so input stays a one-way WebSocket send, not a fake RPC.
    this.socket.send(
      JSON.stringify({
        op: 'terminal.input',
        stream_id: batch.streamId,
        session_id: batch.sessionId,
        lease_id: batch.leaseId,
        fence: batch.fence,
        input_sequence: batch.inputSequence,
        encoding: 'base64',
        data: batch.data,
      }),
    );
    return true;
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

  close(): void {
    this.closed = true;
    this.reconnectCancel?.();
    this.socket?.close();
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new ConnectionLostError('application client closed'));
    }
    this.pending.clear();
    const error = new ConnectionLostError('application client closed');
    for (const subscription of this.projections.values()) {
      if (!subscription.ready) subscription.reject(error);
    }
    this.projections.clear();
    this.terminals.clear();
  }

  private async request(
    kind: 'query' | 'command',
    name: string,
    params: unknown,
  ): Promise<Record<string, unknown>> {
    await this.connect();
    const requestId = `request-${randomUUID()}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        reject(new Error(`request ${name} timed out`));
      }, 31_000);
      this.pending.set(requestId, { resolve, reject, timer });
      this.send({
        op: 'request',
        request_id: requestId,
        timeout_s: 30,
        request: {
          kind,
          name: name as QueryName & CommandName,
          params: params as Record<string, unknown>,
        } as ApplicationRequest,
      });
    });
  }

  private open(): Promise<void> {
    return new Promise((resolve, reject) => {
      const Factory = (
        globalThis as unknown as {
          WebSocket?: new (url: string) => WebSocketLike;
        }
      ).WebSocket;
      if (this.makeSocket === undefined && Factory === undefined) {
        reject(new Error('this Node runtime has no WebSocket implementation'));
        return;
      }
      const socket =
        this.makeSocket === undefined
          ? new (Factory as new (url: string) => WebSocketLike)(this.url)
          : this.makeSocket(this.url);
      this.socket = socket;
      socket.onopen = () =>
        this.send({
          op: 'client.hello',
          protocol_version: APPLICATION_PROTOCOL_VERSION,
          client: { client_id: this.clientId, kind: this.kind },
        });
      socket.onmessage = (event: { data: unknown }) => {
        const message = parseServerMessage(event.data);
        if (message === undefined) return;
        if (message.op === 'server.hello') {
          const serverChanged = this.serverId !== undefined && this.serverId !== message.server_id;
          this.serverId = message.server_id;
          this.connectionGeneration += 1;
          this.projectionCursor = message.projection_cursor;
          this.factCursor = message.fact_cursor;
          if (serverChanged) {
            for (const terminal of this.terminals.values()) {
              terminal.sequence = 0;
              terminal.suspended = false;
              terminal.resyncPending = false;
            }
          }
          this.connecting = undefined;
          resolve();
          for (const subscription of this.projections.values())
            this.reconcileSubscription(subscription);
          for (const terminal of this.terminals.values()) this.reconcileTerminal(terminal);
          this.connected.forEach((listener) => listener());
          return;
        }
        this.dispatch(message);
      };
      socket.onerror = () => {
        if (this.connecting !== undefined)
          reject(new Error('application WebSocket connection failed'));
      };
      socket.onclose = () => {
        this.socket = undefined;
        this.connecting = undefined;
        if (!this.closed) {
          for (const terminal of this.terminals.values()) {
            terminal.resyncPending = false;
            if (terminal.sequence > 0) terminal.suspended = true;
          }
          this.disconnected.forEach((listener) => listener());
          for (const pending of this.pending.values()) {
            clearTimeout(pending.timer);
            pending.reject(new ConnectionLostError());
          }
          this.pending.clear();
          const retry = this.clock.sleep(250);
          this.reconnectCancel = retry.cancel;
          void retry.promise.then(() => this.connect().catch(() => {}));
        }
      };
    });
  }

  private dispatch(message: ServerMessage): void {
    if (message.op === 'reply') {
      const pending = this.pending.get(message.request_id);
      if (pending !== undefined) {
        this.pending.delete(message.request_id);
        clearTimeout(pending.timer);
        pending.resolve(message.result);
      }
    } else if (message.op === 'notification') {
      if (message.type === 'plan.seed_failed') {
        for (const listener of this.planSeedFailed) listener(message);
      }
    } else if (message.op === 'error') {
      if (message.request_id !== null && message.request_id !== undefined) {
        const pending = this.pending.get(message.request_id);
        if (pending !== undefined) {
          this.pending.delete(message.request_id);
          clearTimeout(pending.timer);
          pending.reject(new Error(message.error.message));
        }
      }
      if (message.stream_id !== undefined && message.stream_id !== null) {
        this.terminalInputWatchers
          .get(message.stream_id)
          ?.forEach((listener) => listener(new Error(message.error.message)));
      }
    } else if (message.op === 'subscription.ready') {
      const subscription = this.projections.get(message.subscription_id);
      if (subscription !== undefined) {
        const alreadyReady = subscription.ready;
        subscription.ready = true;
        for (const item of message.snapshot.replay) {
          if (isInvalidation(item.payload)) subscription.invalidation?.(item.payload);
        }
        const reply = {
          snapshots: message.snapshot.snapshots,
          cursor: message.snapshot.cursor,
          mode: message.snapshot.mode,
        };
        subscription.resolve(reply);
        if (alreadyReady) subscription.snapshotListener?.(reply);
      }
    } else if (message.op === 'subscription.event') {
      const subscription = this.projections.get(message.subscription_id);
      if (subscription !== undefined) {
        if (message.cursor !== null && message.cursor !== undefined) {
          subscription.cursor = Math.max(subscription.cursor ?? 0, message.cursor);
        }
        if (isInvalidation(message.payload)) subscription.invalidation?.(message.payload);
      }
    } else if (message.op === 'terminal.frame') {
      const terminal = this.terminals.get(message.stream_id);
      if (terminal !== undefined) this.acceptTerminalFrame(terminal, message.frame);
    } else if (message.op === 'terminal.keyframe') {
      const terminal = this.terminals.get(message.stream_id);
      if (terminal !== undefined) this.acceptTerminalKeyframe(terminal, message.keyframe, false);
    } else if (message.op === 'terminal.chunk') {
      const terminal = this.terminals.get(message.stream_id);
      if (terminal !== undefined) this.acceptTerminalChunk(terminal, message.chunk);
    } else if (message.op === 'terminal.gap') {
      const terminal = this.terminals.get(message.stream_id);
      if (terminal !== undefined) {
        terminal.suspended = true;
        terminal.listener(message.gap);
        this.requestTerminalResync(terminal, 'gap');
      }
    } else if (message.op === 'terminal.resynced') {
      const terminal = this.terminals.get(message.stream_id);
      if (terminal !== undefined) this.acceptTerminalKeyframe(terminal, message.keyframe, true);
    }
  }

  private subscribe(subscription: ProjectionSubscription): void {
    this.send({
      op: 'subscribe',
      subscription_id: subscription.id,
      subscription: {
        kind: 'projections',
        topics: subscription.topics,
        ...(subscription.cursor === undefined ? {} : { cursor: subscription.cursor }),
      },
    });
  }
  private reconcileSubscription(subscription: ProjectionSubscription): void {
    if (subscription.subscribedGeneration === this.connectionGeneration) return;
    subscription.subscribedGeneration = this.connectionGeneration;
    this.subscribe(subscription);
  }
  private unsubscribe(id: string): void {
    this.projections.delete(id);
    this.send({ op: 'unsubscribe', subscription_id: id });
  }
  private attach(terminal: TerminalSubscription): void {
    if (terminal.sessionId === null) return;
    this.send({
      op: 'terminal.attach',
      stream_id: terminal.id,
      target: { session_id: terminal.sessionId },
      after_sequence: terminal.sequence,
      mode: terminal.mode,
    });
    // An already-observed stream must not resume deltas until a full parser
    // state arrives; the attach watermark still lets a server retain/replay.
    if (terminal.sequence > 0) this.requestTerminalResync(terminal, 'reconnect');
  }
  private reconcileTerminal(terminal: TerminalSubscription): void {
    if (terminal.attachedGeneration === this.connectionGeneration) return;
    terminal.attachedGeneration = this.connectionGeneration;
    this.attach(terminal);
  }
  private acceptTerminalFrame(terminal: TerminalSubscription, frame: TerminalFrame): void {
    if (frame.sequence <= terminal.sequence) return;
    terminal.sequence = frame.sequence;
    terminal.suspended = false;
    terminal.resyncPending = false;
    terminal.listener(frame);
  }
  private acceptTerminalKeyframe(
    terminal: TerminalSubscription,
    keyframe: import('../generated/applicationProtocol.js').TerminalKeyframe,
    resynced: boolean,
  ): void {
    if (keyframe.sequence <= terminal.sequence) return;
    terminal.sequence = keyframe.sequence;
    terminal.suspended = false;
    terminal.resyncPending = false;
    terminal.listener(resynced ? { type: 'terminal.resynced', keyframe } : keyframe);
  }
  private acceptTerminalChunk(
    terminal: TerminalSubscription,
    chunk: import('../generated/applicationProtocol.js').TerminalChunk,
  ): void {
    if (chunk.sequence <= terminal.sequence || terminal.suspended) return;
    const expected = terminal.sequence + 1;
    if (chunk.sequence !== expected) {
      terminal.suspended = true;
      terminal.listener({
        type: 'terminal.gap',
        expected_sequence: expected,
        next_sequence: chunk.sequence,
      });
      this.requestTerminalResync(terminal, 'gap');
      return;
    }
    terminal.sequence = chunk.sequence;
    terminal.listener(chunk);
  }
  private requestTerminalResync(
    terminal: TerminalSubscription,
    reason: 'gap' | 'reconnect' | 'unsupported_mode',
  ): void {
    if (terminal.resyncPending) return;
    terminal.resyncPending = true;
    this.send({
      op: 'terminal.resync',
      stream_id: terminal.id,
      after_sequence: terminal.sequence,
      request: 'keyframe',
      reason,
    });
  }
  private send(message: ClientMessage): void {
    if (this.socket?.readyState === 1) this.socket.send(JSON.stringify(message));
  }
}

function isInvalidation(payload: object): payload is ProjectionInvalidation {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    (payload as { type?: unknown }).type === 'projection.invalidate'
  );
}

function parseServerMessage(data: unknown): ServerMessage | undefined {
  if (typeof data !== 'string' || data.trim() === '') return undefined;
  try {
    const parsed = JSON.parse(data) as { op?: unknown };
    return typeof parsed.op === 'string' && SERVER_OPS.has(parsed.op)
      ? (parsed as ServerMessage)
      : undefined;
  } catch {
    return undefined;
  }
}

const SERVER_OPS = new Set([
  'server.hello',
  'reply',
  'notification',
  'subscription.ready',
  'subscription.event',
  'terminal.attached',
  'terminal.frame',
  'terminal.keyframe',
  'terminal.chunk',
  'terminal.gap',
  'terminal.resynced',
  'terminal.input_ack',
  'error',
]);
