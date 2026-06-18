/**
 * WsBusClient tests: handshake framing, RPC correlation, and snapshot fan-out, driven by a mock
 * WebSocket. These pin the bridge contract (one envelope per text frame, no trailing newline) and
 * the protocol behaviors mirrored from UdsBusClient.
 */

import type { BusEvent, WireMessage } from '@core/bus/protocol.js';
import { PROTOCOL_VERSION } from '@core/bus/protocol.js';
import { describe, expect, it } from 'vitest';
import {
  type Clock,
  type WebSocketLike,
  WsBusClient,
} from '../src/bus/WsBusClient.js';

/** A controllable mock WebSocket: records sent frames, lets the test push inbound frames and drive
 * open/close. `readyState` starts CONNECTING (0); the test calls `open()` to fire `onopen`. */
class MockWebSocket implements WebSocketLike {
  readyState = 0; // CONNECTING
  readonly sent: string[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = 3; // CLOSED
    this.onclose?.({});
  }
  open(): void {
    this.readyState = 1; // OPEN
    this.onopen?.({});
  }
  receive(message: WireMessage): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  /** Parse the i-th sent frame back into a WireMessage. */
  sentMessage(i: number): WireMessage {
    const raw = this.sent[i];
    if (raw === undefined) {
      throw new Error(`no sent frame at index ${i}`);
    }
    return JSON.parse(raw) as WireMessage;
  }
}

/** A clock whose `sleep` never resolves (so reconnect backoff doesn't fire during a test) and whose
 * `random` is deterministic. */
const INERT_CLOCK: Clock = {
  sleep: () => ({ promise: new Promise<void>(() => {}), cancel: () => {} }),
  random: () => 0.5,
};

/** Build a client wired to a fresh MockWebSocket, return both. */
function makeClient(): { bus: WsBusClient; socket: MockWebSocket } {
  let socket!: MockWebSocket;
  const bus = new WsBusClient({
    url: 'ws://test/bus',
    clientId: 'web-test',
    clock: INERT_CLOCK,
    webSocketFactory: () => {
      socket = new MockWebSocket();
      return socket;
    },
  });
  // Trigger construction of the socket via connect().
  void bus.connect().catch(() => {});
  return { bus, socket };
}

/** Drive a successful handshake: open the socket, then ack the hello frame. */
function completeHandshake(socket: MockWebSocket): void {
  socket.open();
  const hello = socket.sentMessage(0);
  expect(hello.op).toBe('hello');
  socket.receive({
    op: 'ack',
    schema_version: PROTOCOL_VERSION,
    correlation_id: hello.correlation_id,
    body: { kind: 'subscribed' },
  });
}

describe('WsBusClient handshake framing', () => {
  it('sends one hello envelope as a single frame with NO trailing newline', async () => {
    const { bus, socket } = makeClient();
    socket.open();
    const raw = socket.sent[0];
    expect(raw).toBeDefined();
    expect(raw?.endsWith('\n')).toBe(false);
    const hello = socket.sentMessage(0);
    expect(hello).toMatchObject({
      op: 'hello',
      schema_version: PROTOCOL_VERSION,
      body: { protocol_version: PROTOCOL_VERSION, client_kind: 'web', client_id: 'web-test' },
    });
    bus.close();
  });

  it('resolves connect() once the matching ack arrives', async () => {
    const { bus, socket } = makeClient();
    const connected = bus.connect();
    completeHandshake(socket);
    await expect(connected).resolves.toBeUndefined();
    bus.close();
  });

  it('rejects connect() permanently on a protocol_version_mismatch err', async () => {
    const { bus, socket } = makeClient();
    const connected = bus.connect();
    socket.open();
    const hello = socket.sentMessage(0);
    socket.receive({
      op: 'err',
      schema_version: PROTOCOL_VERSION,
      correlation_id: hello.correlation_id,
      body: { code: 'protocol_version_mismatch', message: 'nope' },
    });
    await expect(connected).rejects.toThrow(/nope/);
    bus.close();
  });
});

describe('WsBusClient RPC correlation', () => {
  it('pairs the ack reply to the outstanding rpc by correlation_id', async () => {
    const { bus, socket } = makeClient();
    const connected = bus.connect();
    completeHandshake(socket);
    await connected;

    const pending = bus.rpc('command.status', { command_id: 'c1' });
    // `rpc` awaits `ensureConnected` (a microtask) before writing the frame, so let that flush.
    await Promise.resolve();
    await Promise.resolve();
    const rpcFrame = socket.sentMessage(socket.sent.length - 1);
    expect(rpcFrame.op).toBe('rpc');

    socket.receive({
      op: 'ack',
      schema_version: PROTOCOL_VERSION,
      correlation_id: rpcFrame.correlation_id,
      body: { kind: 'rpc_reply', result: { ok: true, status: 'done' } },
    });
    await expect(pending).resolves.toMatchObject({ ok: true, status: 'done' });
    bus.close();
  });

  it('rejects an rpc when the server returns an err envelope', async () => {
    const { bus, socket } = makeClient();
    const connected = bus.connect();
    completeHandshake(socket);
    await connected;

    const pending = bus.rpc('command.status', { command_id: 'c1' });
    await Promise.resolve();
    await Promise.resolve();
    const rpcFrame = socket.sentMessage(socket.sent.length - 1);
    socket.receive({
      op: 'err',
      schema_version: PROTOCOL_VERSION,
      correlation_id: rpcFrame.correlation_id,
      body: { code: 'boom', message: 'failed' },
    });
    await expect(pending).rejects.toThrow(/boom/);
    bus.close();
  });
});

describe('WsBusClient snapshot fan-out', () => {
  it('delivers a matching pub event to a subscriber and re-sends sub on reconnect', async () => {
    const { bus, socket } = makeClient();
    const connected = bus.connect();
    completeHandshake(socket);
    await connected;

    const seen: BusEvent[] = [];
    bus.subscribe((ev) => seen.push(ev), { type: 'state.snapshot' });

    // A sub frame was sent for the subscription.
    const subFrame = socket.sentMessage(socket.sent.length - 1);
    expect(subFrame.op).toBe('sub');

    const event: BusEvent = {
      type: 'state.snapshot',
      id: 'e1',
      ts: '2026-06-13T00:00:00Z',
      run_id: 'r',
      agent_id: 'a',
      entity: 'agent',
      key: 'agent:a',
      entity_version: 1,
    };
    socket.receive({
      op: 'pub',
      schema_version: PROTOCOL_VERSION,
      correlation_id: 'pub-1',
      event,
    });
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ type: 'state.snapshot', entity: 'agent' });
    bus.close();
  });

  it('drops a non-matching pub event by the subscription filter', async () => {
    const { bus, socket } = makeClient();
    const connected = bus.connect();
    completeHandshake(socket);
    await connected;

    const seen: BusEvent[] = [];
    // Filter to ticket-only snapshots; an agent snapshot must not reach this listener.
    bus.subscribe((ev) => seen.push(ev), { type: 'state.snapshot', entity: 'ticket' });

    socket.receive({
      op: 'pub',
      schema_version: PROTOCOL_VERSION,
      correlation_id: 'pub-1',
      event: {
        type: 'state.snapshot',
        id: 'e1',
        ts: '2026-06-13T00:00:00Z',
        run_id: 'r',
        agent_id: 'a',
        entity: 'agent',
        key: 'agent:a',
        entity_version: 1,
      },
    });
    expect(seen).toHaveLength(0);
    bus.close();
  });
});
