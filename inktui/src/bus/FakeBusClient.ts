/** In-memory test double for the service-owned application protocol seam. */

import type { ProjectionTopic } from '../generated/applicationProtocol.js';
import type {
  BusClient,
  BusEventListener,
  CommandMethod,
  CommandParams,
  CommandResult,
  HydrateReply,
  HydrateResult,
  ProjectionTopics,
  QueryMethod,
  QueryParams,
  QueryResult,
  TerminalFrame,
  TerminalFrameListener,
  Unsubscribe,
} from './BusClient.js';
import type { BusEvent } from './protocol.js';
import { unwrapReadReply } from './readEnvelope.js';

type ErasedHandler = (params: unknown) => unknown;

interface Hydration {
  readonly listener: BusEventListener | undefined;
  pending: boolean;
  tailBuffer: BusEvent[];
}

interface TerminalAttachment {
  readonly sessionId: string | null;
  readonly listener: TerminalFrameListener;
}

export interface RecordedQueryCall {
  readonly name: QueryMethod;
  readonly params: unknown;
}

export interface RecordedCommandCall {
  readonly name: CommandMethod;
  readonly params: unknown;
}

export interface RecordedHydrateCall {
  readonly topics: readonly ProjectionTopic[];
  readonly cursor: number | null;
}

export interface RecordedTerminalAttach {
  readonly sessionId: string | null;
}

export type QueryHandler<M extends QueryMethod> = (
  params: QueryParams<M>,
) => QueryResult<M> | Promise<QueryResult<M>>;
export type CommandHandler<M extends CommandMethod> = (
  params: CommandParams<M>,
) => CommandResult<M> | Promise<CommandResult<M>>;
export type HydrateHandler = (
  topics: readonly ProjectionTopic[],
  cursor: number | null,
) => HydrateReply | Promise<HydrateReply>;

export class FakeBusClient implements BusClient {
  private readonly queryHandlers = new Map<QueryMethod, ErasedHandler>();
  private readonly commandHandlers = new Map<CommandMethod, ErasedHandler>();
  private readonly recordedQueries: RecordedQueryCall[] = [];
  private readonly recordedCommands: RecordedCommandCall[] = [];
  private readonly recordedHydrates: RecordedHydrateCall[] = [];
  private readonly recordedTerminals: RecordedTerminalAttach[] = [];
  private readonly hydrations = new Set<Hydration>();
  private readonly terminals = new Set<TerminalAttachment>();
  private hydrateHandler: HydrateHandler | undefined;
  private cursor: number | null = null;

  stubQuery<M extends QueryMethod>(name: M, reply: QueryResult<M> | QueryHandler<M>): void {
    const handler: QueryHandler<M> =
      typeof reply === 'function' ? (reply as QueryHandler<M>) : () => reply;
    this.queryHandlers.set(name, handler as ErasedHandler);
  }

  stubCommand<M extends CommandMethod>(name: M, reply: CommandResult<M> | CommandHandler<M>): void {
    const handler: CommandHandler<M> =
      typeof reply === 'function' ? (reply as CommandHandler<M>) : () => reply;
    this.commandHandlers.set(name, handler as ErasedHandler);
  }

  stubHydrate(reply: HydrateReply | HydrateHandler): void {
    this.hydrateHandler =
      typeof reply === 'function' ? (reply as HydrateHandler) : async () => reply;
  }

  get queryCalls(): readonly RecordedQueryCall[] {
    return [...this.recordedQueries];
  }

  get commandCalls(): readonly RecordedCommandCall[] {
    return [...this.recordedCommands];
  }

  get hydrateCalls(): readonly RecordedHydrateCall[] {
    return [...this.recordedHydrates];
  }

  get terminalAttachCalls(): readonly RecordedTerminalAttach[] {
    return [...this.recordedTerminals];
  }

  get subscriberCount(): number {
    return this.hydrations.size;
  }

  get terminalSubscriberCount(): number {
    return this.terminals.size;
  }

  query<M extends QueryMethod>(name: M, params: QueryParams<M>): Promise<QueryResult<M>> {
    this.recordedQueries.push({ name, params });
    const handler = this.queryHandlers.get(name);
    if (handler === undefined) {
      return Promise.reject(new Error(`FakeBusClient: no query stub for '${name}'`));
    }
    return Promise.resolve()
      .then(() => handler(params))
      .then((reply) => unwrapReadReply(name, reply) as QueryResult<M>);
  }

  command<M extends CommandMethod>(name: M, params: CommandParams<M>): Promise<CommandResult<M>> {
    this.recordedCommands.push({ name, params });
    const handler = this.commandHandlers.get(name);
    if (handler === undefined) {
      return Promise.reject(new Error(`FakeBusClient: no command stub for '${name}'`));
    }
    return Promise.resolve().then(() => handler(params) as CommandResult<M>);
  }

  hydrate(topics: ProjectionTopics, listener?: BusEventListener): Promise<HydrateResult> {
    const normalized = normalizeProjectionTopics(topics);
    const callCursor = this.cursor;
    this.recordedHydrates.push({ topics: normalized, cursor: callCursor });
    const hydration: Hydration = { listener, pending: true, tailBuffer: [] };
    this.hydrations.add(hydration);
    const reply =
      this.hydrateHandler === undefined
        ? Promise.resolve<HydrateReply>({ snapshots: {}, cursor: callCursor })
        : Promise.resolve().then(() => this.hydrateHandler?.(normalized, callCursor));
    return reply.then((value) => {
      const resolved = value ?? { snapshots: {}, cursor: callCursor };
      this.observeCursor(resolved.cursor);
      for (const item of resolved.replay ?? []) {
        this.observeCursor(item.seq);
        hydration.listener?.(item.event);
      }
      for (const event of hydration.tailBuffer) {
        hydration.listener?.(event);
      }
      hydration.tailBuffer = [];
      hydration.pending = false;
      return {
        ...resolved,
        unsubscribe: () => this.hydrations.delete(hydration),
      };
    });
  }

  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe {
    const attachment: TerminalAttachment = { sessionId, listener };
    this.recordedTerminals.push({ sessionId });
    this.terminals.add(attachment);
    return () => this.terminals.delete(attachment);
  }

  /** Emit a projection or notification compatibility payload. */
  emit(event: BusEvent, cursor?: number | null): void {
    this.observeCursor(cursor);
    for (const hydration of [...this.hydrations]) {
      if (hydration.pending && event.type !== 'error') {
        hydration.tailBuffer.push(event);
      } else {
        hydration.listener?.(event);
      }
    }
  }

  emitTerminal(sessionId: string | null, frame: TerminalFrame | string, sequence = 1): void {
    const value: TerminalFrame =
      typeof frame === 'string'
        ? {
            mode: 'replace',
            sequence,
            session_id: sessionId ?? 'supervisor',
            frame,
          }
        : frame;
    for (const attachment of [...this.terminals]) {
      if (attachment.sessionId === sessionId) {
        attachment.listener(value);
      }
    }
  }

  private observeCursor(cursor: number | null | undefined): void {
    if (typeof cursor !== 'number') return;
    this.cursor = this.cursor === null ? cursor : Math.max(this.cursor, cursor);
  }
}

function normalizeProjectionTopics(topics: ProjectionTopics): readonly ProjectionTopic[] {
  return typeof topics === 'string' ? [topics] : [...topics];
}
