/** Browser client tests for the closed application WebSocket protocol. */

import type { ClientMessage, ServerMessage } from '@core/generated/applicationProtocol.js';
import { APPLICATION_PROTOCOL_VERSION } from '@core/generated/applicationProtocol.js';
import { describe, expect, it } from 'vitest';
import {
  type Clock,
  type WebSocketLike,
  ApplicationWebSocketClient,
} from '../src/application/ApplicationWebSocketClient.js';

class MockWebSocket implements WebSocketLike {
  readyState = 0;
  readonly sent: string[] = [];
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  send(data: string): void { this.sent.push(data); }
  close(): void { this.readyState = 3; this.onclose?.({}); }
  open(): void { this.readyState = 1; this.onopen?.({}); }
  receive(message: ServerMessage): void { this.onmessage?.({ data: JSON.stringify(message) }); }
  message(index: number): ClientMessage { return JSON.parse(this.sent[index] ?? '') as ClientMessage; }
}

const INERT_CLOCK: Clock = {
  sleep: () => ({ promise: new Promise<void>(() => {}), cancel: () => {} }),
  random: () => 0.5,
};

function makeClient(): { client: ApplicationWebSocketClient; socket: MockWebSocket } {
  const socket = new MockWebSocket();
  const client = new ApplicationWebSocketClient({
    url: 'ws://test/api/ws',
    clientId: 'web-test',
    clock: INERT_CLOCK,
    webSocketFactory: () => socket,
  });
  return { client, socket };
}

async function connect(client: ApplicationWebSocketClient, socket: MockWebSocket): Promise<void> {
  const pending = client.connect();
  socket.open();
  expect(socket.message(0)).toEqual({
    op: 'client.hello',
    protocol_version: APPLICATION_PROTOCOL_VERSION,
    client: { client_id: 'web-test', kind: 'web' },
  });
  socket.receive({
    op: 'server.hello', protocol_version: APPLICATION_PROTOCOL_VERSION, server_id: 'service-test',
    queries: ['settings.get'], commands: ['settings.update'], subscriptions: ['projections'],
    terminal_streams: true, fact_cursor: 2, projection_cursor: 3,
  });
  await pending;
}

async function flush(): Promise<void> { await Promise.resolve(); await Promise.resolve(); }

describe('ApplicationWebSocketClient', () => {
  it('uses the sole application endpoint and closed hello frame', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    expect(client.getFactCursor()).toBe(2);
    expect(client.getProjectionCursor()).toBe(3);
    client.close();
  });

  it('correlates a typed application query reply', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const pending = client.query('settings.get', {});
    await flush();
    const request = socket.message(1);
    expect(request).toMatchObject({ op: 'request', request: { kind: 'query', name: 'settings.get' } });
    if (request.op !== 'request') throw new Error('expected request');
    socket.receive({ op: 'reply', request_id: request.request_id, result: { ok: true, settings: {} } });
    await expect(pending).resolves.toMatchObject({ ok: true });
    client.close();
  });

  it('subscribes only to feature projection invalidations', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const invalidations: unknown[] = [];
    const hydration = client.hydrate('roster', (event) => invalidations.push(event), null);
    await flush();
    const subscription = socket.message(1);
    expect(subscription).toMatchObject({ op: 'subscribe', subscription: { kind: 'projections', topics: ['roster'] } });
    expect(socket.sent).toHaveLength(2);
    if (subscription.op !== 'subscribe') throw new Error('expected subscription');
    socket.receive({
      op: 'subscription.ready', subscription_id: subscription.subscription_id,
      snapshot: {
        snapshots: {
          roster: { sessions: [], as_of: '2026-07-23T00:00:00Z', invalidation_key: 'roster-1' },
        },
        cursor: 3,
        mode: 'cold',
        replay: [],
      },
    });
    const ready = await hydration;
    socket.receive({
      op: 'subscription.event', subscription_id: subscription.subscription_id, cursor: 4,
      payload: { type: 'projection.invalidate', projection: 'roster', subject_key: 'all', generation: 1, source_fact_id: null },
    });
    expect(invalidations).toHaveLength(1);
    ready.unsubscribe();
    expect(socket.message(2)).toEqual({ op: 'unsubscribe', subscription_id: subscription.subscription_id });
    client.close();
  });

  it('attaches terminals by session_id without a legacy target adapter', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const sessionId = '0198b156-2dd3-70a9-bc79-fca001dc8801';
    const detach = client.attachTerminal(sessionId, () => {});
    expect(socket.message(1)).toMatchObject({ op: 'terminal.attach', target: { session_id: sessionId } });
    detach();
    client.close();
  });
});
