/** Shared application WebSocket client transport tests. */

import { APPLICATION_PROTOCOL_VERSION } from '../../src/generated/applicationProtocol.js';
import type { ClientMessage, ServerMessage } from '../../src/generated/applicationProtocol.js';
import { describe, expect, it } from 'vitest';
import {
  type Clock,
  type WebSocketLike,
  ApplicationWebSocketClient,
  ConnectionLostError,
} from '../../src/application/ApplicationWebSocketClient.js';

class MockWebSocket implements WebSocketLike {
  readyState = 0;
  readonly sent: string[] = [];
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
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
  message(index: number): ClientMessage {
    return JSON.parse(this.sent[index] ?? '') as ClientMessage;
  }
}

/** Sleep resolves immediately so reconnect loops are testable without real timers. */
const FAST_CLOCK: Clock = {
  sleep: () => ({ promise: Promise.resolve(), cancel: () => {} }),
  random: () => 0.5,
};

function makeClient(kind: 'tui' | 'web' = 'tui'): {
  client: ApplicationWebSocketClient;
  sockets: MockWebSocket[];
  current(): MockWebSocket;
} {
  const sockets: MockWebSocket[] = [];
  const client = new ApplicationWebSocketClient({
    url: 'ws://test/api/ws',
    clientId: `${kind}-test`,
    kind,
    clock: FAST_CLOCK,
    webSocketFactory: () => {
      const socket = new MockWebSocket();
      sockets.push(socket);
      return socket;
    },
  });
  return {
    client,
    sockets,
    current: () => {
      const socket = sockets[sockets.length - 1];
      if (socket === undefined) throw new Error('no socket');
      return socket;
    },
  };
}

function hello(): ServerMessage {
  return {
    op: 'server.hello',
    protocol_version: APPLICATION_PROTOCOL_VERSION,
    server_id: 'service-test',
    queries: ['settings.get'],
    commands: ['settings.update'],
    subscriptions: ['projections'],
    terminal_streams: true,
    fact_cursor: 2,
    projection_cursor: 3,
  };
}

async function connect(
  client: ApplicationWebSocketClient,
  getSocket: () => MockWebSocket,
  kind: 'tui' | 'web' = 'tui',
): Promise<MockWebSocket> {
  const pending = client.connect();
  await flush();
  const socket = getSocket();
  socket.open();
  expect(socket.message(0)).toEqual({
    op: 'client.hello',
    protocol_version: APPLICATION_PROTOCOL_VERSION,
    client: { client_id: `${kind}-test`, kind },
  });
  socket.receive(hello());
  await pending;
  return socket;
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe('ApplicationWebSocketClient', () => {
  it('uses the sole application endpoint and closed hello frame', async () => {
    const { client, current } = makeClient('tui');
    await connect(client, current, 'tui');
    expect(client.getFactCursor()).toBe(2);
    expect(client.getProjectionCursor()).toBe(3);
    client.close();
  });

  it('correlates a typed application query reply', async () => {
    const { client, current } = makeClient();
    const socket = await connect(client, current);
    const pending = client.query('settings.get', {});
    await flush();
    const request = socket.message(1);
    expect(request).toMatchObject({
      op: 'request',
      request: { kind: 'query', name: 'settings.get' },
    });
    if (request.op !== 'request') throw new Error('expected request');
    socket.receive({
      op: 'reply',
      request_id: request.request_id,
      result: { ok: true, settings: {} },
    });
    await expect(pending).resolves.toMatchObject({ ok: true });
    client.close();
  });

  it('advances the subscription cursor so reconnect resumes from the last event', async () => {
    const { client, current, sockets } = makeClient();
    const socket = await connect(client, current);
    const hydration = client.hydrate('roster', () => {}, null);
    await flush();
    const subscription = socket.message(1);
    if (subscription.op !== 'subscribe') throw new Error('expected subscription');
    socket.receive({
      op: 'subscription.ready',
      subscription_id: subscription.subscription_id,
      snapshot: {
        snapshots: {
          roster: { sessions: [], as_of: '2026-07-23T00:00:00Z', invalidation_key: 'roster-1' },
        },
        cursor: 3,
        mode: 'cold',
        replay: [],
      },
    });
    await hydration;
    socket.receive({
      op: 'subscription.event',
      subscription_id: subscription.subscription_id,
      cursor: 4,
      payload: {
        type: 'projection.invalidate',
        projection: 'roster',
        subject_key: 'all',
        generation: 1,
        source_fact_id: null,
      },
    });

    socket.close();
    await flush();
    expect(sockets.length).toBeGreaterThanOrEqual(2);
    const reconnected = current();
    reconnected.open();
    reconnected.receive(hello());
    await flush();

    const resume = reconnected.sent
      .map((raw) => JSON.parse(raw) as ClientMessage)
      .find((message) => message.op === 'subscribe');
    expect(resume).toMatchObject({
      op: 'subscribe',
      subscription: { kind: 'projections', topics: ['roster'], cursor: 4 },
    });
    client.close();
  });

  it('delivers snapshot_fallback through the snapshot listener after initial settle', async () => {
    const { client, current } = makeClient();
    const socket = await connect(client, current);
    const snapshots: unknown[] = [];
    const hydration = client.hydrate('roster', undefined, null, (reply) => {
      snapshots.push(reply);
    });
    await flush();
    const subscription = socket.message(1);
    if (subscription.op !== 'subscribe') throw new Error('expected subscription');
    socket.receive({
      op: 'subscription.ready',
      subscription_id: subscription.subscription_id,
      snapshot: {
        snapshots: {
          roster: { sessions: [], as_of: '2026-07-23T00:00:00Z', invalidation_key: 'roster-1' },
        },
        cursor: 3,
        mode: 'cold',
        replay: [],
      },
    });
    await hydration;
    expect(snapshots).toHaveLength(0);

    socket.receive({
      op: 'subscription.ready',
      subscription_id: subscription.subscription_id,
      snapshot: {
        snapshots: {
          roster: {
            sessions: [],
            as_of: '2026-07-23T00:01:00Z',
            invalidation_key: 'roster-2',
          },
        },
        cursor: 9,
        mode: 'snapshot_fallback',
        replay: [],
      },
    });
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0]).toMatchObject({ mode: 'snapshot_fallback', cursor: 9 });
    client.close();
  });

  it('fails pending requests immediately on disconnect', async () => {
    const { client, current } = makeClient();
    await connect(client, current);
    const pending = client.query('settings.get', {});
    await flush();
    current().close();
    await expect(pending).rejects.toBeInstanceOf(ConnectionLostError);
    client.close();
  });

  it('ignores non-protocol inbound frames', async () => {
    const { client, current } = makeClient();
    const socket = await connect(client, current);
    socket.onmessage?.({ data: '{"op":"not.a.real.op"}' });
    socket.onmessage?.({ data: 'not-json' });
    client.close();
  });

  it('attaches terminals by session_id', async () => {
    const { client, current } = makeClient();
    const socket = await connect(client, current);
    const sessionId = '0198b156-2dd3-70a9-bc79-fca001dc8801';
    const detach = client.attachTerminal(sessionId, () => {});
    expect(socket.message(1)).toMatchObject({
      op: 'terminal.attach',
      target: { session_id: sessionId },
    });
    detach();
    client.close();
  });
});
