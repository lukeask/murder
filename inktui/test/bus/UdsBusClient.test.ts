import { randomUUID } from 'node:crypto';
import { rm } from 'node:fs/promises';
import { createServer, type Server, type Socket } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { BusEvent } from '../../src/bus/protocol.js';
import {
  type BackoffConfig,
  type Clock,
  ConnectionLostError,
  LineBuffer,
  ProtocolVersionMismatchError,
  RpcTimeoutError,
  UdsBusClient,
} from '../../src/bus/UdsBusClient.js';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ClientMessage,
  type ServerMessage,
  type SubscribeMessage,
} from '../../src/generated/applicationProtocol.js';

class ScriptedApplicationServer {
  readonly socketPath = join(tmpdir(), `inktui-application-${randomUUID()}.sock`);
  readonly messages: ClientMessage[] = [];
  handshakeCount = 0;
  rejectVersion = false;
  requestHandler: (
    message: Extract<ClientMessage, { op: 'request' }>,
  ) => Record<string, unknown> | undefined = () => ({});
  snapshot: Extract<ServerMessage, { op: 'subscription.ready' }>['snapshot'] = {
    snapshots: {},
    cursor: 1,
    mode: 'cold',
    replay: [],
  };

  private server: Server | undefined;
  private readonly sockets = new Set<Socket>();

  async start(): Promise<void> {
    await rm(this.socketPath, { force: true });
    this.server = createServer((socket) => this.handleConnection(socket));
    await new Promise<void>((resolve, reject) => {
      this.server?.once('error', reject);
      this.server?.listen(this.socketPath, () => {
        this.server?.removeListener('error', reject);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    for (const socket of this.sockets) socket.destroy();
    this.sockets.clear();
    if (this.server !== undefined) {
      await new Promise<void>((resolve) => this.server?.close(() => resolve()));
      this.server = undefined;
    }
    await rm(this.socketPath, { force: true });
  }

  dropAllConnections(): void {
    for (const socket of this.sockets) socket.destroy();
    this.sockets.clear();
  }

  writeRaw(data: string): void {
    for (const socket of this.sockets) socket.write(data);
  }

  emitProjection(payload: BusEvent, cursor = 2): void {
    const subscription = this.latestSubscription('projections');
    if (subscription === undefined) throw new Error('no projection subscription');
    this.broadcast({
      op: 'subscription.event',
      subscription_id: subscription.subscription_id,
      cursor,
      payload: payload as unknown as Record<string, unknown>,
    });
  }

  emitNotification(payload: BusEvent, cursor = 2): void {
    const subscription = this.latestSubscription('notifications');
    if (subscription === undefined) throw new Error('no notification subscription');
    this.broadcast({
      op: 'subscription.event',
      subscription_id: subscription.subscription_id,
      cursor,
      payload: payload as unknown as Record<string, unknown>,
    });
  }

  emitTerminal(frame = 'screen', sequence = 1): void {
    const attach = [...this.messages]
      .reverse()
      .find(
        (message): message is Extract<ClientMessage, { op: 'terminal.attach' }> =>
          message.op === 'terminal.attach',
      );
    if (attach === undefined) throw new Error('no terminal attachment');
    this.broadcast({
      op: 'terminal.frame',
      stream_id: attach.stream_id,
      frame: {
        type: 'terminal.frame',
        subscription_id: attach.stream_id,
        sequence,
        session_id: attach.target.session_id ?? 'supervisor',
        captured_at: '2026-07-18T00:00:00Z',
        columns: 80,
        rows: 24,
        encoding: 'utf-8',
        data: frame,
        reset: true,
      },
    });
  }

  emitTerminalChunk(data: string, sequence: number): void {
    const attach = [...this.messages]
      .reverse()
      .find(
        (message): message is Extract<ClientMessage, { op: 'terminal.attach' }> =>
          message.op === 'terminal.attach',
      );
    if (attach === undefined) throw new Error('no terminal attachment');
    this.broadcast({
      op: 'terminal.chunk',
      stream_id: attach.stream_id,
      chunk: {
        type: 'terminal.chunk',
        subscription_id: attach.stream_id,
        session_id: attach.target.session_id ?? 'supervisor',
        sequence,
        encoding: 'utf-8',
        data,
      },
    });
  }

  emitTerminalGap(expectedSequence: number, nextSequence: number): void {
    const attach = [...this.messages]
      .reverse()
      .find(
        (message): message is Extract<ClientMessage, { op: 'terminal.attach' }> =>
          message.op === 'terminal.attach',
      );
    if (attach === undefined) throw new Error('no terminal attachment');
    this.broadcast({
      op: 'terminal.gap',
      stream_id: attach.stream_id,
      gap: {
        type: 'terminal.gap',
        subscription_id: attach.stream_id,
        session_id: attach.target.session_id ?? 'supervisor',
        expected_sequence: expectedSequence,
        next_sequence: nextSequence,
        snapshot_required: true,
      },
    });
  }

  latestSubscription(kind: 'projections' | 'notifications'): SubscribeMessage | undefined {
    return [...this.messages]
      .reverse()
      .find(
        (message): message is SubscribeMessage =>
          message.op === 'subscribe' && message.subscription.kind === kind,
      );
  }

  private handleConnection(socket: Socket): void {
    this.sockets.add(socket);
    const lines = new LineBuffer();
    socket.on('data', (chunk: Buffer) => {
      for (const line of lines.push(chunk.toString('utf8'))) {
        this.handleMessage(socket, JSON.parse(line) as ClientMessage);
      }
    });
    socket.on('close', () => this.sockets.delete(socket));
    socket.on('error', () => this.sockets.delete(socket));
  }

  private handleMessage(socket: Socket, message: ClientMessage): void {
    this.messages.push(message);
    switch (message.op) {
      case 'client.hello':
        if (this.rejectVersion) {
          this.send(socket, {
            op: 'error',
            error: {
              code: 'version_mismatch',
              message: 'application protocol mismatch',
              details: {},
            },
          });
          return;
        }
        this.handshakeCount += 1;
        this.send(socket, {
          op: 'server.hello',
          protocol_version: APPLICATION_PROTOCOL_VERSION,
          server_id: 'test-server',
          queries: ['roster.get', 'ticket.get'],
          commands: ['plan.create'],
          subscriptions: ['projections', 'notifications'],
          terminal_streams: true,
        });
        return;
      case 'request': {
        const result = this.requestHandler(message);
        if (result !== undefined) {
          this.send(socket, { op: 'reply', request_id: message.request_id, result });
        }
        return;
      }
      case 'subscribe':
        this.send(socket, {
          op: 'subscription.ready',
          subscription_id: message.subscription_id,
          snapshot:
            message.subscription.kind === 'projections'
              ? this.snapshot
              : { snapshots: {}, cursor: this.snapshot.cursor, mode: 'cold', replay: [] },
        });
        return;
      case 'terminal.attach':
        this.send(socket, {
          op: 'terminal.attached',
          stream_id: message.stream_id,
          mode: 'replace',
        });
        return;
      case 'unsubscribe':
      case 'terminal.detach':
      case 'terminal.resync':
        return;
      default:
        assertNever(message);
    }
  }

  private send(socket: Socket, message: ServerMessage): void {
    socket.write(`${JSON.stringify(message)}\n`);
  }

  private broadcast(message: ServerMessage): void {
    for (const socket of this.sockets) this.send(socket, message);
  }
}

function assertNever(value: never): never {
  throw new Error(`unexpected client message: ${JSON.stringify(value)}`);
}

function instantClock(random = 0): Clock {
  return {
    sleep: () => ({ promise: Promise.resolve(), cancel: () => {} }),
    random: () => random,
  };
}

const FAST_BACKOFF: BackoffConfig = { baseMs: 1, capMs: 4 };

function snapshot(key: string): BusEvent {
  return {
    type: 'state.snapshot',
    id: `evt-${key}`,
    ts: '2026-06-08T00:00:00Z',
    run_id: 'run-1',
    agent_id: '',
    entity: 'ticket',
    key,
    entity_version: 1,
  };
}

function errorEvent(message: string): BusEvent {
  return {
    type: 'error',
    id: `err-${message}`,
    ts: '2026-06-08T00:00:00Z',
    run_id: 'run-1',
    agent_id: '',
    message,
    recoverable: true,
  };
}

async function waitFor(predicate: () => boolean, timeoutMs = 2000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('waitFor: condition not met before timeout');
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

describe('UdsBusClient — generated application handshake', () => {
  let server: ScriptedApplicationServer;
  let client: UdsBusClient | undefined;

  beforeEach(async () => {
    server = new ScriptedApplicationServer();
    await server.start();
  });
  afterEach(async () => {
    client?.close();
    await server.stop();
  });

  it('sends client.hello and accepts server.hello', async () => {
    client = new UdsBusClient({
      socketPath: server.socketPath,
      clientId: 'tui-test',
      clock: instantClock(),
    });
    await client.connect();

    expect(server.messages[0]).toEqual({
      op: 'client.hello',
      protocol_version: APPLICATION_PROTOCOL_VERSION,
      client: { client_id: 'tui-test', kind: 'tui' },
    });
    expect(server.handshakeCount).toBe(1);
  });

  it('treats a version_mismatch error as permanent', async () => {
    server.rejectVersion = true;
    client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    const errors: Error[] = [];
    client.onPermanentError((error) => errors.push(error));

    await expect(client.connect()).rejects.toBeInstanceOf(ProtocolVersionMismatchError);
    await waitFor(() => errors.length === 1);
    expect(server.messages.filter((message) => message.op === 'client.hello')).toHaveLength(1);
  });
});

describe('UdsBusClient — generated request/reply', () => {
  let server: ScriptedApplicationServer;
  let client: UdsBusClient;

  beforeEach(async () => {
    server = new ScriptedApplicationServer();
    await server.start();
    client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
  });
  afterEach(async () => {
    client.close();
    await server.stop();
  });

  it('sends a generated query request and unwraps its read envelope', async () => {
    server.requestHandler = (message) => ({
      ok: true,
      value: { invalidation_key: 'iv', sessions: [], echoed: message.request.name },
    });

    const result = await client.query('roster.get', {});
    expect(result).toMatchObject({ invalidation_key: 'iv', echoed: 'roster.get' });
    expect(server.messages).toContainEqual(
      expect.objectContaining({
        op: 'request',
        request: { kind: 'query', name: 'roster.get', params: {} },
      }),
    );
  });

  it('sends a generated command request without unwrapping the result', async () => {
    server.requestHandler = () => ({ handled: true, ok: true, plan_name: 'phase-1' });

    const result = await client.command('plan.create', {
      plan_name: 'phase-1',
      body: '# Phase 1',
    });
    expect(result).toMatchObject({ handled: true, plan_name: 'phase-1' });
    expect(server.messages).toContainEqual(
      expect.objectContaining({
        op: 'request',
        request: {
          kind: 'command',
          name: 'plan.create',
          params: { plan_name: 'phase-1', body: '# Phase 1' },
        },
      }),
    );
  });

  it('times out a request the server never replies to', async () => {
    server.requestHandler = () => undefined;
    const timeoutClient = new UdsBusClient({
      socketPath: server.socketPath,
      clock: instantClock(),
      rpcTimeoutS: -0.99,
    });
    await expect(timeoutClient.query('roster.get', {})).rejects.toBeInstanceOf(RpcTimeoutError);
    timeoutClient.close();
  });
});

describe('UdsBusClient — generated subscriptions', () => {
  let server: ScriptedApplicationServer;
  let client: UdsBusClient;

  beforeEach(async () => {
    server = new ScriptedApplicationServer();
    await server.start();
    client = new UdsBusClient({
      socketPath: server.socketPath,
      clock: instantClock(),
      backoff: FAST_BACKOFF,
    });
  });
  afterEach(async () => {
    client.close();
    await server.stop();
  });

  it('hydrates from subscription.ready and sends projection + notification subscriptions', async () => {
    server.snapshot = {
      snapshots: { roster: { invalidation_key: 'iv', sessions: [] } },
      cursor: 9,
      mode: 'cold',
      replay: [],
    };

    const hydration = await client.hydrate(['roster', 'schedule']);
    expect(hydration).toMatchObject({
      snapshots: { roster: { invalidation_key: 'iv', sessions: [] } },
      cursor: 9,
      mode: 'cold',
    });
    expect(server.latestSubscription('projections')?.subscription).toEqual({
      kind: 'projections',
      topics: ['roster', 'schedule'],
    });
    expect(server.latestSubscription('notifications')?.subscription).toEqual({
      kind: 'notifications',
      channels: ['errors'],
    });
  });

  it('preserves compatibility DTO events in subscription replay and tail delivery', async () => {
    server.snapshot = {
      snapshots: {},
      cursor: 10,
      mode: 'resume',
      replay: [
        {
          cursor: 9,
          payload: snapshot('T-replay') as unknown as Record<string, unknown>,
        },
      ],
    };
    const received: BusEvent[] = [];
    await client.hydrate('schedule', (event) => received.push(event));
    server.emitProjection(snapshot('T-tail'), 11);
    await waitFor(() => received.length === 2);

    expect(received.map((event) => (event as { key: string }).key)).toEqual(['T-replay', 'T-tail']);
  });

  it('delivers compatibility notification events through the standing hydration listener', async () => {
    const received: BusEvent[] = [];
    await client.hydrate('roster', (event) => received.push(event));
    server.emitNotification(errorEvent('service warning'));
    await waitFor(() => received.length === 1);
    expect(received[0]).toMatchObject({ type: 'error', message: 'service warning' });
  });

  it('sends unsubscribe for both generated subscriptions', async () => {
    const hydration = await client.hydrate('roster');
    const projectionId = server.latestSubscription('projections')?.subscription_id;
    const notificationId = server.latestSubscription('notifications')?.subscription_id;
    hydration.unsubscribe();
    await waitFor(
      () => server.messages.filter((message) => message.op === 'unsubscribe').length === 2,
    );

    const ids = server.messages
      .filter(
        (message): message is Extract<ClientMessage, { op: 'unsubscribe' }> =>
          message.op === 'unsubscribe',
      )
      .map((message) => message.subscription_id);
    expect(ids).toEqual([projectionId, notificationId]);
  });

  it('re-sends subscribe with the last cursor after reconnect', async () => {
    await client.hydrate('roster');
    server.emitProjection(snapshot('T-2'), 12);
    server.dropAllConnections();
    await waitFor(() => server.handshakeCount === 2);
    await waitFor(
      () =>
        server.messages.filter(
          (message) => message.op === 'subscribe' && message.subscription.kind === 'projections',
        ).length === 2,
    );

    expect(server.latestSubscription('projections')?.subscription).toMatchObject({ cursor: 12 });
  });
});

describe('UdsBusClient — generated terminal stream', () => {
  it('sends terminal.attach/detach and delivers terminal.frame', async () => {
    const server = new ScriptedApplicationServer();
    await server.start();
    const client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    const frames: string[] = [];

    const detach = client.attachTerminal('agent-1', (frame) => frames.push(frame.data));
    await waitFor(() => server.messages.some((message) => message.op === 'terminal.attach'));
    expect(server.messages.find((message) => message.op === 'terminal.attach')).toMatchObject({
      op: 'terminal.attach',
      target: { legacy_agent_id: 'agent-1' },
      after_sequence: 0,
    });

    server.emitTerminal('terminal contents');
    await waitFor(() => frames.length === 1);
    expect(frames).toEqual(['terminal contents']);

    detach();
    await waitFor(() => server.messages.some((message) => message.op === 'terminal.detach'));
    client.close();
    await server.stop();
  });

  it('attaches a durable session UUID without treating it as an agent id', async () => {
    const server = new ScriptedApplicationServer();
    await server.start();
    const client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    const sessionId = '0198b156-2dd3-70a9-bc79-fca001dc8801';

    const detach = client.attachTerminal(sessionId, () => {});
    await waitFor(() => server.messages.some((message) => message.op === 'terminal.attach'));
    expect(server.messages.find((message) => message.op === 'terminal.attach')).toMatchObject({
      target: { session_id: sessionId },
    });

    detach();
    client.close();
    await server.stop();
  });

  it('requests a full resync for an incremental gap and resumes attachment sequence', async () => {
    const server = new ScriptedApplicationServer();
    await server.start();
    const client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    const updates: string[] = [];

    client.attachTerminal('agent-1', (update) => updates.push(update.data));
    await waitFor(() => server.messages.some((message) => message.op === 'terminal.attach'));
    server.emitTerminal('snapshot', 7);
    await waitFor(() => updates.length === 1);

    server.emitTerminalChunk('missed predecessor', 9);
    await waitFor(() => server.messages.some((message) => message.op === 'terminal.resync'));
    expect(server.messages.find((message) => message.op === 'terminal.resync')).toMatchObject({
      op: 'terminal.resync',
      after_sequence: 7,
      reason: 'gap',
    });
    expect(updates).toEqual(['snapshot']);

    server.dropAllConnections();
    await waitFor(() => server.handshakeCount === 2);
    await waitFor(
      () => server.messages.filter((message) => message.op === 'terminal.attach').length === 2,
    );
    const attachments = server.messages.filter(
      (message): message is Extract<ClientMessage, { op: 'terminal.attach' }> =>
        message.op === 'terminal.attach',
    );
    expect(attachments.at(-1)?.after_sequence).toBe(7);

    client.close();
    await server.stop();
  });
});

describe('UdsBusClient — framing and lifecycle', () => {
  it('reassembles partial JSON-lines and splits multiple frames', () => {
    const buffer = new LineBuffer();
    expect(buffer.push('{"op":"server.')).toEqual([]);
    expect(buffer.push('hello"}\n{"op":"reply"}\n')).toEqual([
      '{"op":"server.hello"}',
      '{"op":"reply"}',
    ]);
  });

  it('logs malformed JSON and rejects outstanding requests when the connection drops', async () => {
    const server = new ScriptedApplicationServer();
    await server.start();
    server.requestHandler = () => undefined;
    const warnings: string[] = [];
    const client = new UdsBusClient({
      socketPath: server.socketPath,
      clock: instantClock(),
      backoff: FAST_BACKOFF,
      logger: { warn: (message) => warnings.push(message), info: () => {} },
    });
    await client.connect();
    server.writeRaw('not-json\n');
    await waitFor(() => warnings.some((message) => message.includes('invalid application JSON')));

    const pending = client.query('roster.get', {});
    const rejected = expect(pending).rejects.toBeInstanceOf(ConnectionLostError);
    await waitFor(() => server.messages.some((message) => message.op === 'request'));
    server.dropAllConnections();
    await rejected;

    client.close();
    await server.stop();
  });

  it('rejects generated requests after close', async () => {
    const server = new ScriptedApplicationServer();
    await server.start();
    const client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    await client.connect();
    client.close();
    await expect(client.query('roster.get', {})).rejects.toBeInstanceOf(ConnectionLostError);
    await server.stop();
  });
});
