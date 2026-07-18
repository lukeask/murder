/** Application-protocol framing and correlation tests for the browser WebSocket client. */

import type {
  ClientMessage,
  ServerMessage,
} from '@core/generated/applicationProtocol.js';
import { APPLICATION_PROTOCOL_VERSION } from '@core/generated/applicationProtocol.js';
import { describe, expect, it } from 'vitest';
import {
  type Clock,
  type WebSocketLike,
  WsBusClient,
} from '../src/bus/WsBusClient.js';

class MockWebSocket implements WebSocketLike {
  readyState = 0;
  readonly sent: string[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.({});
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  receive(message: ServerMessage): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  sentMessage(index: number): ClientMessage {
    const raw = this.sent[index];
    if (raw === undefined) throw new Error(`no sent frame at index ${index}`);
    return JSON.parse(raw) as ClientMessage;
  }
}

const INERT_CLOCK: Clock = {
  sleep: () => ({ promise: new Promise<void>(() => {}), cancel: () => {} }),
  random: () => 0.5,
};

function makeClient(clock: Clock = INERT_CLOCK): {
  bus: WsBusClient;
  sockets: MockWebSocket[];
} {
  const sockets: MockWebSocket[] = [];
  const bus = new WsBusClient({
    url: 'ws://test/api/ws',
    clientId: 'web-test',
    clock,
    webSocketFactory: () => {
      const socket = new MockWebSocket();
      sockets.push(socket);
      return socket;
    },
  });
  void bus.connect().catch(() => {});
  return { bus, sockets };
}

function completeHandshake(socket: MockWebSocket): void {
  socket.open();
  expect(socket.sentMessage(0)).toEqual({
    op: 'client.hello',
    protocol_version: APPLICATION_PROTOCOL_VERSION,
    client: { client_id: 'web-test', kind: 'web' },
  });
  socket.receive({
    op: 'server.hello',
    protocol_version: APPLICATION_PROTOCOL_VERSION,
    server_id: 'service-test',
    queries: ['settings.get'],
    commands: ['settings.update'],
    subscriptions: ['projections', 'notifications'],
    terminal_streams: true,
  });
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe('WsBusClient application handshake', () => {
  it('uses /api/ws and sends client.hello as one WebSocket frame without a newline', () => {
    const { bus, sockets } = makeClient();
    sockets[0]?.open();
    expect(sockets[0]?.sent[0]?.endsWith('\n')).toBe(false);
    expect(sockets[0]?.sentMessage(0).op).toBe('client.hello');
    bus.close();
  });

  it('resolves connect on server.hello and permanently rejects a version mismatch', async () => {
    const { bus, sockets } = makeClient();
    const connected = bus.connect();
    completeHandshake(sockets[0]!);
    await expect(connected).resolves.toBeUndefined();
    bus.close();

    const mismatch = makeClient();
    const failed = mismatch.bus.connect();
    mismatch.sockets[0]!.open();
    mismatch.sockets[0]!.receive({
      op: 'error',
      error: { code: 'version_mismatch', message: 'upgrade required', details: {} },
    });
    await expect(failed).rejects.toThrow(/upgrade required/);
    mismatch.bus.close();
  });
});

describe('WsBusClient requests', () => {
  it('sends a closed query request and correlates its reply by request_id', async () => {
    const { bus, sockets } = makeClient();
    completeHandshake(sockets[0]!);
    await bus.connect();

    const pending = bus.query('settings.get', {});
    await flush();
    const request = sockets[0]!.sentMessage(1);
    expect(request).toMatchObject({
      op: 'request',
      request: { kind: 'query', name: 'settings.get', params: {} },
    });
    if (request.op !== 'request') throw new Error('expected request');
    sockets[0]!.receive({
      op: 'reply',
      request_id: request.request_id,
      result: { ok: true, settings: {} },
    });
    await expect(pending).resolves.toMatchObject({ ok: true });
    bus.close();
  });

  it('rejects a request-scoped application error', async () => {
    const { bus, sockets } = makeClient();
    completeHandshake(sockets[0]!);
    await bus.connect();
    const pending = bus.query('settings.get', {});
    await flush();
    const request = sockets[0]!.sentMessage(1);
    if (request.op !== 'request') throw new Error('expected request');
    sockets[0]!.receive({
      op: 'error',
      request_id: request.request_id,
      error: { code: 'request_failed', message: 'failed', details: {} },
    });
    await expect(pending).rejects.toThrow(/request_failed/);
    bus.close();
  });
});

describe('WsBusClient resumable streams', () => {
  it('hydrates projections, tails errors separately, and unsubscribes both streams', async () => {
    const { bus, sockets } = makeClient();
    completeHandshake(sockets[0]!);
    await bus.connect();

    const seen: unknown[] = [];
    const hydration = bus.hydrate('roster', (event) => seen.push(event));
    await flush();
    const projection = sockets[0]!.sentMessage(1);
    const notifications = sockets[0]!.sentMessage(2);
    expect(projection).toMatchObject({
      op: 'subscribe',
      subscription: { kind: 'projections', topics: ['roster'], cursor: null },
    });
    expect(notifications).toMatchObject({
      op: 'subscribe',
      subscription: { kind: 'notifications', channels: ['errors'], cursor: null },
    });
    if (projection.op !== 'subscribe' || notifications.op !== 'subscribe') {
      throw new Error('expected subscriptions');
    }
    sockets[0]!.receive({
      op: 'subscription.ready',
      subscription_id: notifications.subscription_id,
      snapshot: { snapshots: {}, cursor: 7, mode: 'cold', replay: [] },
    });
    sockets[0]!.receive({
      op: 'subscription.ready',
      subscription_id: projection.subscription_id,
      snapshot: {
        snapshots: { roster: { rows: [] } },
        cursor: 8,
        mode: 'cold',
        replay: [],
      },
    });
    const ready = await hydration;
    expect(ready.snapshots).toEqual({ roster: { rows: [] } });

    sockets[0]!.receive({
      op: 'subscription.event',
      subscription_id: notifications.subscription_id,
      cursor: 9,
      payload: { type: 'error', message: 'boom' },
    });
    expect(seen).toEqual([{ type: 'error', message: 'boom' }]);

    ready.unsubscribe();
    expect(sockets[0]!.sent.slice(-2).map((raw) => JSON.parse(raw))).toEqual([
      { op: 'unsubscribe', subscription_id: projection.subscription_id },
      { op: 'unsubscribe', subscription_id: notifications.subscription_id },
    ]);
    bus.close();
  });

  it('uses terminal.attach and the disposer sends terminal.detach', async () => {
    const { bus, sockets } = makeClient();
    completeHandshake(sockets[0]!);
    await bus.connect();
    const frames: string[] = [];
    const detach = bus.attachTerminal('agent-a', (frame) => frames.push(frame.frame));
    const attach = sockets[0]!.sentMessage(1);
    expect(attach).toMatchObject({
      op: 'terminal.attach',
      target: { session_id: 'agent-a' },
    });
    if (attach.op !== 'terminal.attach') throw new Error('expected terminal attach');
    sockets[0]!.receive({
      op: 'terminal.frame',
      stream_id: attach.stream_id,
      frame: {
        mode: 'replace',
        sequence: 1,
        session_id: 'agent-a',
        frame: 'hello',
      },
    });
    expect(frames).toEqual(['hello']);
    detach();
    expect(sockets[0]!.sentMessage(2)).toEqual({
      op: 'terminal.detach',
      stream_id: attach.stream_id,
    });
    bus.close();
  });

  it('reattaches projection/error cursors and terminal intent after reconnect', async () => {
    const immediateClock: Clock = {
      sleep: () => ({ promise: Promise.resolve(), cancel: () => {} }),
      random: () => 0,
    };
    const { bus, sockets } = makeClient(immediateClock);
    completeHandshake(sockets[0]!);
    await bus.connect();

    const hydration = bus.hydrate('roster');
    const detach = bus.attachTerminal('agent-a', () => {});
    const projection = sockets[0]!.sentMessage(1);
    const notifications = sockets[0]!.sentMessage(2);
    if (projection.op !== 'subscribe' || notifications.op !== 'subscribe') {
      throw new Error('expected subscriptions');
    }
    sockets[0]!.receive({
      op: 'subscription.ready',
      subscription_id: notifications.subscription_id,
      snapshot: { snapshots: {}, cursor: 7, mode: 'cold', replay: [] },
    });
    sockets[0]!.receive({
      op: 'subscription.ready',
      subscription_id: projection.subscription_id,
      snapshot: { snapshots: {}, cursor: 8, mode: 'cold', replay: [] },
    });
    const ready = await hydration;

    sockets[0]!.close();
    await flush();
    await flush();
    expect(sockets).toHaveLength(2);
    completeHandshake(sockets[1]!);
    await flush();

    const resent = sockets[1]!.sent.slice(1).map((raw) => JSON.parse(raw) as ClientMessage);
    expect(resent).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          op: 'subscribe',
          subscription_id: projection.subscription_id,
          subscription: expect.objectContaining({ kind: 'projections', cursor: 8 }),
        }),
        expect.objectContaining({
          op: 'subscribe',
          subscription_id: notifications.subscription_id,
          subscription: expect.objectContaining({ kind: 'notifications', cursor: 7 }),
        }),
        expect.objectContaining({
          op: 'terminal.attach',
          target: { session_id: 'agent-a' },
        }),
      ]),
    );
    ready.unsubscribe();
    detach();
    bus.close();
  });
});
