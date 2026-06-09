import { randomUUID } from 'node:crypto';
import { rm } from 'node:fs/promises';
import { createServer, type Server, type Socket } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { RpcMethod } from '../../src/bus/BusClient.js';
import { type BusEvent, PROTOCOL_VERSION, type WireMessage } from '../../src/bus/protocol.js';
import {
  type BackoffConfig,
  type Clock,
  ConnectionLostError,
  LineBuffer,
  ProtocolVersionMismatchError,
  RpcTimeoutError,
  UdsBusClient,
} from '../../src/bus/UdsBusClient.js';

// The C2 test idiom: stand up a real in-process Unix-socket server (`net.createServer`) on a temp
// path that speaks the handshake + a scripted reply/event, point a UdsBusClient at it, and assert
// on the observable behavior. No live murder service is needed — the server here *is* the contract.

/**
 * A scriptable in-process bus server. It performs the real Hello/Ack handshake (so the client's
 * framing/correlation must be correct) and exposes hooks for tests to drive RPCs, events, version
 * mismatch, and connection drops.
 */
class ScriptedBusServer {
  readonly socketPath: string;
  private server: Server | undefined;
  private connections = new Set<Socket>();

  /** Set to refuse the next handshake with a version mismatch. */
  rejectVersion = false;
  /** Number of handshakes completed — lets a test assert a reconnect re-handshaked. */
  handshakeCount = 0;
  /** Every `sub` correlation_id seen, in order — lets a test assert re-subscription on reconnect. */
  readonly subscribeCorrelationIds: string[] = [];
  /** RPC handler: given (target, body), returns a reply object, or `undefined` to stay silent
   * (drives the timeout path). */
  rpcHandler: (
    target: string,
    body: Record<string, unknown>,
  ) => Record<string, unknown> | undefined = () => ({});

  constructor() {
    this.socketPath = join(tmpdir(), `inktui-bus-${randomUUID()}.sock`);
  }

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
    for (const socket of this.connections) {
      socket.destroy();
    }
    this.connections.clear();
    if (this.server !== undefined) {
      await new Promise<void>((resolve) => this.server?.close(() => resolve()));
      this.server = undefined;
    }
    await rm(this.socketPath, { force: true });
  }

  /** Forcibly drop every live client connection (simulates a service restart / socket loss). */
  dropAllConnections(): void {
    for (const socket of this.connections) {
      socket.destroy();
    }
    this.connections.clear();
  }

  /** Push a `pub` event frame to every connected client. */
  emit(event: BusEvent): void {
    const frame: WireMessage = {
      op: 'pub',
      schema_version: PROTOCOL_VERSION,
      correlation_id: `pub-${randomUUID()}`,
      event,
    };
    this.broadcast(frame);
  }

  /** When set, the next handshake writes the Ack and a pipelined `pub` event in a *single*
   * `socket.write()` — forcing them into one TCP segment / `data` event so the client's handshake
   * read sees them in the same chunk. This reproduces the pipelined-frame drop. */
  pipelineEventWithAck: BusEvent | undefined;

  /** Send an interleaved `wake` frame to every client (the client must skip these). */
  emitWake(): void {
    const frame: WireMessage = {
      op: 'wake',
      schema_version: PROTOCOL_VERSION,
      correlation_id: '',
      body: { client_id: 'test', reason: 'connect', fresh_state_hints: [] },
    };
    this.broadcast(frame);
  }

  private broadcast(frame: WireMessage): void {
    const line = `${JSON.stringify(frame)}\n`;
    for (const socket of this.connections) {
      socket.write(line);
    }
  }

  private handleConnection(socket: Socket): void {
    this.connections.add(socket);
    const buffer = new LineBuffer();
    socket.on('data', (chunk: Buffer) => {
      for (const line of buffer.push(chunk.toString('utf8'))) {
        this.handleLine(socket, line);
      }
    });
    socket.on('close', () => this.connections.delete(socket));
    socket.on('error', () => this.connections.delete(socket));
  }

  private handleLine(socket: Socket, line: string): void {
    const message = JSON.parse(line) as WireMessage;
    switch (message.op) {
      case 'hello': {
        if (this.rejectVersion) {
          this.send(socket, {
            op: 'err',
            schema_version: PROTOCOL_VERSION,
            correlation_id: message.correlation_id,
            body: {
              code: 'protocol_version_mismatch',
              message: `server=${PROTOCOL_VERSION} client=${message.body.protocol_version}`,
              details: {},
            },
          });
          return;
        }
        this.handshakeCount += 1;
        if (this.pipelineEventWithAck !== undefined) {
          // Pipeline path: write the Ack *and* a subsequent `pub` frame in ONE write() so they
          // share a single chunk on the client. The trailing event must not be dropped.
          const ackLine = `${JSON.stringify({
            op: 'ack',
            schema_version: PROTOCOL_VERSION,
            correlation_id: message.correlation_id,
            body: { kind: 'subscribed' },
          })}\n`;
          const pubLine = `${JSON.stringify({
            op: 'pub',
            schema_version: PROTOCOL_VERSION,
            correlation_id: `pub-${randomUUID()}`,
            event: this.pipelineEventWithAck,
          })}\n`;
          socket.write(ackLine + pubLine);
          return;
        }
        // Reply Ack, then an interleaved wake the client must already tolerate post-handshake.
        this.send(socket, {
          op: 'ack',
          schema_version: PROTOCOL_VERSION,
          correlation_id: message.correlation_id,
          body: { kind: 'subscribed' },
        });
        this.send(socket, {
          op: 'wake',
          schema_version: PROTOCOL_VERSION,
          correlation_id: '',
          body: { client_id: message.body.client_id, reason: 'connect', fresh_state_hints: [] },
        });
        return;
      }
      case 'sub': {
        this.subscribeCorrelationIds.push(message.correlation_id);
        this.send(socket, {
          op: 'ack',
          schema_version: PROTOCOL_VERSION,
          correlation_id: message.correlation_id,
          body: { kind: 'subscribed' },
        });
        return;
      }
      case 'rpc': {
        const reply = this.rpcHandler(message.args.target, message.args.body);
        if (reply === undefined) {
          return; // stay silent → drives the client timeout path
        }
        this.send(socket, {
          op: 'ack',
          schema_version: PROTOCOL_VERSION,
          correlation_id: message.correlation_id,
          body: { kind: 'rpc_reply', result: reply },
        });
        return;
      }
      default:
        return;
    }
  }

  private send(socket: Socket, message: WireMessage): void {
    socket.write(`${JSON.stringify(message)}\n`);
  }
}

/** A controllable clock so reconnect backoff is deterministic and instant in tests. `sleep`
 * resolves on the next microtask; `random` is fixed so jitter is predictable. */
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

/** Poll until `predicate` holds or the deadline elapses — for asserting on async server state
 * (handshake counts, sub re-establishment) without arbitrary sleeps. */
async function waitFor(predicate: () => boolean, timeoutMs = 2000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) {
      throw new Error('waitFor: condition not met before timeout');
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

describe('UdsBusClient — handshake', () => {
  let server: ScriptedBusServer;
  let client: UdsBusClient | undefined;

  beforeEach(async () => {
    server = new ScriptedBusServer();
    await server.start();
  });
  afterEach(async () => {
    client?.close();
    await server.stop();
  });

  it('completes the Hello/Ack handshake against a real socket', async () => {
    client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    await expect(client.connect()).resolves.toBeUndefined();
    expect(server.handshakeCount).toBe(1);
  });

  it('refuses the connection on a protocol-version mismatch (not retried)', async () => {
    server.rejectVersion = true;
    client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    await expect(client.connect()).rejects.toBeInstanceOf(ProtocolVersionMismatchError);
    // Permanent: it must not have retried into a second handshake attempt.
    expect(server.handshakeCount).toBe(0);
  });
});

describe('UdsBusClient — rpc', () => {
  let server: ScriptedBusServer;
  let client: UdsBusClient;

  beforeEach(async () => {
    server = new ScriptedBusServer();
    await server.start();
    client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
  });
  afterEach(async () => {
    client.close();
    await server.stop();
  });

  it('round-trips an rpc paired by correlation_id', async () => {
    server.rpcHandler = (target, body) => ({ echoed: target, ...body });
    const result = await client.rpc('ticket.quick_kick', { ticket_id: 'T-42' });
    expect(result).toEqual({ echoed: 'ticket.quick_kick', ticket_id: 'T-42' });
  });

  it('unwraps the read-RPC `{ ok, value }` envelope for `state.*` methods', async () => {
    // The service wraps read-handler DTOs as `{ ok: true, value: <dto> }` (`_value()` in host.py);
    // the store reads read replies top-level, so the client unwraps `.value` for `state.*` methods.
    server.rpcHandler = () => ({ ok: true, value: { sessions: ['c1', 'c2'] } });
    // `state.*` read methods are augmented onto RpcMethods in the store slices; cast here so this
    // transport-level test stays independent of the store layer.
    const result = await client.rpc('state.crow_snapshot' as RpcMethod, {} as never);
    expect(result).toEqual({ sessions: ['c1', 'c2'] });
  });

  it('preserves a `value: null` read envelope as `null` (not-found signal)', async () => {
    // `_state_ticket_detail` returns `_value(None)` → `{ ok: true, value: null }` for a missing
    // ticket; the store keys not-found on that `null`, so the unwrap must NOT coerce it to `{}`.
    server.rpcHandler = () => ({ ok: true, value: null });
    const result = await client.rpc('state.ticket_detail' as RpcMethod, {} as never);
    expect(result).toBeNull();
  });

  it('leaves a write/command reply (no `value` key) untouched', async () => {
    // Writes return `{ ok, ...fields }` top-level and must NOT be unwrapped.
    server.rpcHandler = () => ({ ok: true, ticket_id: 'T-9' });
    const result = await client.rpc('ticket.quick_kick', { ticket_id: 'T-9' });
    expect(result).toEqual({ ok: true, ticket_id: 'T-9' });
  });

  it('rejects an rpc that the server never answers (timeout)', async () => {
    // Inject a tiny RPC timeout (rule 4) so the deadline path is exercised in real time, no fake
    // timers. The server stays silent, so the only way the promise settles is the timeout.
    const timeoutClient = new UdsBusClient({
      socketPath: server.socketPath,
      clock: instantClock(),
      rpcTimeoutS: 0.01,
    });
    server.rpcHandler = () => undefined;
    await expect(
      timeoutClient.rpc('agent.message', { agent_id: 'a1', message: 'hi' }),
    ).rejects.toBeInstanceOf(RpcTimeoutError);
    timeoutClient.close();
  });
});

describe('UdsBusClient — framing', () => {
  it('reassembles a wire message split across two socket reads (partial frame)', () => {
    // LineBuffer is the inbound framing unit; assert it directly for the partial-frame case.
    const buffer = new LineBuffer();
    expect(buffer.push('{"op":"a')).toEqual([]);
    expect(buffer.push('ck"}\n{"op":')).toEqual(['{"op":"ack"}']);
    expect(buffer.push('"err"}\n')).toEqual(['{"op":"err"}']);
  });

  it('splits multiple messages arriving in one chunk', () => {
    const buffer = new LineBuffer();
    expect(buffer.push('{"a":1}\n{"b":2}\n{"c":3}\n')).toEqual(['{"a":1}', '{"b":2}', '{"c":3}']);
  });
});

describe('UdsBusClient — subscriptions', () => {
  let server: ScriptedBusServer;
  let client: UdsBusClient;

  beforeEach(async () => {
    server = new ScriptedBusServer();
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

  it('delivers pushed events to a subscriber', async () => {
    const received: BusEvent[] = [];
    client.subscribe((event) => received.push(event));
    await waitFor(() => server.subscribeCorrelationIds.length === 1);

    server.emit(snapshot('T-1'));
    await waitFor(() => received.length === 1);

    expect((received[0] as { key: string }).key).toBe('T-1');
  });

  it('skips interleaved wake frames (does not deliver them as events)', async () => {
    const received: BusEvent[] = [];
    client.subscribe((event) => received.push(event));
    await waitFor(() => server.subscribeCorrelationIds.length === 1);

    server.emitWake();
    server.emit(snapshot('T-1'));
    await waitFor(() => received.length === 1);

    // Only the snapshot, never the wake.
    expect(received).toHaveLength(1);
    expect((received[0] as { type: string }).type).toBe('state.snapshot');
  });

  it('delivers a pub frame pipelined into the handshake-ack chunk (no drop)', async () => {
    // Regression: the server writes the Hello-ack AND a pub event in a SINGLE write(), so they
    // land in one chunk during the client's handshake read. The handshake must complete AND the
    // pipelined event must reach the subscriber (it must not be dropped with the handshake buffer).
    server.pipelineEventWithAck = snapshot('T-pipelined');
    const received: BusEvent[] = [];
    // Subscribe before connecting (as the other sub tests do) so the listener exists when the
    // pipelined frame is dispatched right after the handshake.
    client.subscribe((event) => received.push(event));

    await client.connect();
    expect(server.handshakeCount).toBe(1);

    await waitFor(() => received.length === 1);
    expect((received[0] as { key: string }).key).toBe('T-pipelined');
  });

  it('stops delivering after unsubscribe', async () => {
    const received: BusEvent[] = [];
    const unsubscribe = client.subscribe((event) => received.push(event));
    await waitFor(() => server.subscribeCorrelationIds.length === 1);

    unsubscribe();
    server.emit(snapshot('T-1'));
    // Give the frame time to (not) arrive.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(received).toHaveLength(0);
  });
});

describe('UdsBusClient — reconnect', () => {
  let server: ScriptedBusServer;
  let client: UdsBusClient;

  beforeEach(async () => {
    server = new ScriptedBusServer();
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

  it('reconnects and re-handshakes after a dropped connection', async () => {
    await client.connect();
    expect(server.handshakeCount).toBe(1);

    server.dropAllConnections();
    await waitFor(() => server.handshakeCount === 2);
    expect(server.handshakeCount).toBe(2);
  });

  it('re-establishes the subscription on reconnect (store never re-subscribes)', async () => {
    const received: BusEvent[] = [];
    client.subscribe((event) => received.push(event));
    await waitFor(() => server.subscribeCorrelationIds.length === 1);
    const firstCorrelation = server.subscribeCorrelationIds[0];

    server.dropAllConnections();
    // After reconnect the client re-sends the sub frame with a fresh correlation id.
    await waitFor(() => server.subscribeCorrelationIds.length === 2);
    expect(server.subscribeCorrelationIds[1]).not.toBe(firstCorrelation);

    // And events on the new connection are delivered to the original listener.
    server.emit(snapshot('T-after-reconnect'));
    await waitFor(() => received.length === 1);
    expect((received[0] as { key: string }).key).toBe('T-after-reconnect');
  });

  it('rejects an outstanding rpc when the connection drops', async () => {
    await client.connect();
    server.rpcHandler = () => undefined; // never answers
    const pending = client.rpc('agent.message', { agent_id: 'a1', message: 'hi' });
    // Attach the rejection assertion before dropping so the rejection is observed.
    const assertion = expect(pending).rejects.toBeInstanceOf(ConnectionLostError);
    await waitFor(() => server.handshakeCount === 1);
    server.dropAllConnections();
    await assertion;
  });
});

describe('UdsBusClient — close', () => {
  it('stops reconnection and rejects rpc after close', async () => {
    const server = new ScriptedBusServer();
    await server.start();
    const client = new UdsBusClient({ socketPath: server.socketPath, clock: instantClock() });
    await client.connect();
    client.close();

    await expect(
      client.rpc('agent.message', { agent_id: 'a1', message: 'hi' }),
    ).rejects.toBeInstanceOf(ConnectionLostError);
    await server.stop();
  });
});
