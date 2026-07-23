/** In-memory test double for the service-owned application protocol seam. */

import type { ProjectionTopic } from '../generated/applicationProtocol.js';
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
  TerminalFrame,
  TerminalFrameListener,
  Unsubscribe,
} from './ApplicationClient.js';
import { unwrapReadReply } from './normalizeReply.js';

type ErasedHandler = (params: unknown) => unknown;
type AnyCommandHandler = (name: CommandMethod, params: unknown) => unknown;

interface Hydration {
  readonly invalidationListener: ProjectionInvalidationListener | undefined;
  pending: boolean;
  invalidationTail: ProjectionInvalidation[];
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

export type QueryHandler<M extends QueryMethod> = (params: QueryParams<M>) => unknown;
export type CommandHandler<M extends CommandMethod> = (params: CommandParams<M>) => unknown;
export type HydrateHandler = (
  topics: readonly ProjectionTopic[],
  cursor: number | null,
) => HydrateReply | Promise<HydrateReply>;

export class FakeApplicationClient implements ApplicationClient {
  private readonly queryHandlers = new Map<QueryMethod, ErasedHandler>();
  private readonly commandHandlers = new Map<CommandMethod, ErasedHandler>();
  private readonly recordedQueries: RecordedQueryCall[] = [];
  private readonly recordedCommands: RecordedCommandCall[] = [];
  private readonly recordedHydrates: RecordedHydrateCall[] = [];
  private readonly recordedTerminals: RecordedTerminalAttach[] = [];
  private readonly hydrations = new Set<Hydration>();
  private readonly terminals = new Set<TerminalAttachment>();
  private hydrateHandler: HydrateHandler | undefined;
  private allCommandsHandler: AnyCommandHandler | undefined;
  private cursor: number | null = null;
  private factCursor: number | undefined;
  private projectionCursor: number | undefined;

  /**
   * Test fixtures represent raw server wire payloads. Accept them as unknown here, while the public
   * query method still returns the generated result type and feature actions narrow at their boundary.
   */
  stubQuery<M extends QueryMethod>(name: M, reply: QueryHandler<M>): void;
  stubQuery<M extends QueryMethod>(name: M, reply: unknown): void;
  stubQuery<M extends QueryMethod>(name: M, reply: unknown | QueryHandler<M>): void {
    const handler: QueryHandler<M> =
      typeof reply === 'function' ? (reply as QueryHandler<M>) : () => reply;
    this.queryHandlers.set(name, handler as ErasedHandler);
  }

  stubCommand<M extends CommandMethod>(name: M, reply: CommandHandler<M>): void;
  stubCommand<M extends CommandMethod>(name: M, reply: unknown): void;
  stubCommand<M extends CommandMethod>(name: M, reply: unknown | CommandHandler<M>): void {
    const handler: CommandHandler<M> =
      typeof reply === 'function' ? (reply as CommandHandler<M>) : () => reply;
    this.commandHandlers.set(name, handler as ErasedHandler);
  }

  /**
   * Supply one default handler for a test that does not care which generated command is issued.
   * Named command stubs still take precedence, so tests can retain precise assertions where they
   * matter without reintroducing a generic command envelope.
   */
  stubAllCommands(reply: unknown | AnyCommandHandler): void {
    this.allCommandsHandler =
      typeof reply === 'function' ? (reply as AnyCommandHandler) : () => reply;
  }

  stubHydrate(reply: HydrateReply | HydrateHandler): void {
    this.hydrateHandler =
      typeof reply === 'function' ? (reply as HydrateHandler) : async () => reply;
  }

  /** Seed hello-style watermarks for tests that assert default-`since` behavior. */
  setHelloCursors(factCursor: number, projectionCursor: number): void {
    this.factCursor = factCursor;
    this.projectionCursor = projectionCursor;
  }

  getFactCursor(): number | undefined {
    return this.factCursor;
  }

  getProjectionCursor(): number | undefined {
    return this.projectionCursor;
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
      return Promise.reject(new Error(`FakeApplicationClient: no query stub for '${name}'`));
    }
    return Promise.resolve()
      .then(() => handler(params))
      .then((reply) => unwrapReadReply(name, reply) as QueryResult<M>);
  }

  command<M extends CommandMethod>(name: M, params: CommandParams<M>): Promise<CommandResult<M>> {
    this.recordedCommands.push({ name, params });
    const handler = this.commandHandlers.get(name);
    const defaultHandler = this.allCommandsHandler;
    if (handler === undefined && defaultHandler === undefined) {
      return Promise.reject(new Error(`FakeApplicationClient: no command stub for '${name}'`));
    }
    return Promise.resolve().then(
      () =>
        (handler === undefined
          ? defaultHandler?.(name, params)
          : handler(params)) as CommandResult<M>,
    );
  }

  hydrate(
    topics: ProjectionTopics,
    invalidationListener?: ProjectionInvalidationListener,
    since?: number | null,
  ): Promise<HydrateResult> {
    const normalized = normalizeProjectionTopics(topics);
    const callCursor = resolveFakeHydrateCursor(since, this.projectionCursor, this.cursor);
    this.recordedHydrates.push({ topics: normalized, cursor: callCursor });
    const hydration: Hydration = {
      invalidationListener,
      pending: true,
      invalidationTail: [],
    };
    this.hydrations.add(hydration);
    const reply =
      this.hydrateHandler === undefined
        ? Promise.resolve<HydrateReply>({ snapshots: {}, cursor: callCursor })
        : Promise.resolve().then(() => this.hydrateHandler?.(normalized, callCursor));
    return reply.then(
      (value) => {
        const resolved = value ?? { snapshots: {}, cursor: callCursor };
        this.observeCursor(resolved.cursor);
        for (const invalidation of hydration.invalidationTail) {
          hydration.invalidationListener?.(invalidation);
        }
        hydration.invalidationTail = [];
        hydration.pending = false;
        return {
          ...resolved,
          unsubscribe: () => this.hydrations.delete(hydration),
        };
      },
      (error: unknown) => {
        this.hydrations.delete(hydration);
        throw error;
      },
    );
  }

  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe {
    const attachment: TerminalAttachment = { sessionId, listener };
    this.recordedTerminals.push({ sessionId });
    this.terminals.add(attachment);
    return () => this.terminals.delete(attachment);
  }

  /** Emit a projection invalidation to standing hydration listeners. */
  emitInvalidation(invalidation: ProjectionInvalidation, cursor?: number | null): void {
    this.observeCursor(cursor);
    for (const hydration of [...this.hydrations]) {
      if (hydration.pending) {
        hydration.invalidationTail.push(invalidation);
      } else {
        hydration.invalidationListener?.(invalidation);
      }
    }
  }

  emitTerminal(sessionId: string | null, frame: TerminalFrame | string, sequence = 1): void {
    const value: TerminalFrame =
      typeof frame === 'string'
        ? {
            type: 'terminal.frame',
            subscription_id: 'fake-terminal',
            sequence,
            session_id: sessionId ?? 'supervisor',
            captured_at: new Date().toISOString(),
            columns: Math.max(1, frame.length),
            rows: Math.max(1, frame.split('\n').length),
            encoding: 'utf-8',
            data: frame,
            reset: true,
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

function resolveFakeHydrateCursor(
  since: number | null | undefined,
  helloProjectionCursor: number | undefined,
  observedCursor: number | null,
): number | null {
  if (since === null) return null;
  if (typeof since === 'number') return since;
  if (helloProjectionCursor !== undefined) return helloProjectionCursor;
  return observedCursor;
}
