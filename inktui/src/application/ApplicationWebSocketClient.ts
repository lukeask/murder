/** Direct WebSocket implementation of Murder's closed application protocol. */

import { randomUUID } from 'node:crypto';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ApplicationRequest,
  type ClientMessage,
  type CommandName,
  type ProjectionTopic,
  type QueryName,
  type ServerMessage,
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
  TerminalFrameListener,
  Unsubscribe,
} from './ApplicationClient.js';
import { unwrapReadReply } from './normalizeReply.js';

type Socket = {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
};

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
  resolve(value: HydrateReply): void;
  reject(reason: Error): void;
}

interface TerminalSubscription {
  id: string;
  sessionId: string | null;
  listener: TerminalFrameListener;
  sequence: number;
}

export class ApplicationWebSocketClient implements ApplicationClient {
  private socket: Socket | undefined;
  private connecting: Promise<void> | undefined;
  private closed = false;
  private readonly pending = new Map<string, Pending>();
  private readonly projections = new Map<string, ProjectionSubscription>();
  private readonly terminals = new Map<string, TerminalSubscription>();
  private readonly connected = new Set<() => void>();
  private readonly disconnected = new Set<() => void>();
  private projectionCursor: number | undefined;
  private factCursor: number | undefined;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly url: string,
    private readonly clientId = `tui-${randomUUID()}`,
  ) {}

  async connect(): Promise<void> {
    if (this.closed) throw new Error('application client is closed');
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
  ): Promise<HydrateResult> {
    const id = `projection-${randomUUID()}`;
    const selected = (Array.isArray(topics) ? topics : [topics]) as ProjectionTopic[];
    const reply = new Promise<HydrateReply>((resolve, reject) => {
      this.projections.set(id, {
        id,
        topics: selected,
        cursor: since === null ? undefined : (since ?? this.projectionCursor),
        invalidation,
        resolve,
        reject,
      });
    });
    await this.connect();
    this.subscribe(this.projections.get(id)!);
    return { ...(await reply), unsubscribe: () => this.unsubscribe(id) };
  }

  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe {
    const id = `terminal-${randomUUID()}`;
    const terminal = { id, sessionId, listener, sequence: 0 };
    this.terminals.set(id, terminal);
    void this.connect().then(() => this.attach(terminal));
    return () => {
      this.terminals.delete(id);
      this.send({ op: 'terminal.detach', stream_id: id });
    };
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer !== undefined) clearTimeout(this.reconnectTimer);
    this.socket?.close();
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new Error('application client closed'));
    }
    this.pending.clear();
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
      const Factory = (globalThis as unknown as { WebSocket?: new (url: string) => Socket })
        .WebSocket;
      if (Factory === undefined) {
        reject(new Error('this Node runtime has no WebSocket implementation'));
        return;
      }
      const socket = new Factory(this.url);
      this.socket = socket;
      socket.onopen = () =>
        this.send({
          op: 'client.hello',
          protocol_version: APPLICATION_PROTOCOL_VERSION,
          client: { client_id: this.clientId, kind: 'tui' },
        });
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as ServerMessage;
        if (message.op === 'server.hello') {
          this.projectionCursor = message.projection_cursor;
          this.factCursor = message.fact_cursor;
          this.connecting = undefined;
          resolve();
          for (const subscription of this.projections.values()) this.subscribe(subscription);
          for (const terminal of this.terminals.values()) this.attach(terminal);
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
          this.disconnected.forEach((listener) => listener());
          this.reconnectTimer = setTimeout(() => {
            void this.connect().catch(() => {});
          }, 250);
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
    } else if (
      message.op === 'error' &&
      message.request_id !== null &&
      message.request_id !== undefined
    ) {
      const pending = this.pending.get(message.request_id);
      if (pending !== undefined) {
        this.pending.delete(message.request_id);
        clearTimeout(pending.timer);
        pending.reject(new Error(message.error.message));
      }
    } else if (message.op === 'subscription.ready') {
      const subscription = this.projections.get(message.subscription_id);
      if (subscription !== undefined) {
        for (const item of message.snapshot.replay) {
          if (isInvalidation(item.payload)) subscription.invalidation?.(item.payload);
        }
        subscription.resolve({
          snapshots: message.snapshot.snapshots,
          cursor: message.snapshot.cursor,
          mode: message.snapshot.mode,
        });
      }
    } else if (message.op === 'subscription.event') {
      const subscription = this.projections.get(message.subscription_id);
      if (subscription !== undefined && isInvalidation(message.payload))
        subscription.invalidation?.(message.payload);
    } else if (message.op === 'terminal.frame') {
      const terminal = this.terminals.get(message.stream_id);
      if (terminal !== undefined && message.frame.sequence > terminal.sequence) {
        terminal.sequence = message.frame.sequence;
        terminal.listener(message.frame);
      }
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
