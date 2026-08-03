/** Browser client tests for the closed application WebSocket protocol. */

import type { ClientMessage, ServerMessage } from '../../src/generated/applicationProtocol.js';
import { APPLICATION_PROTOCOL_VERSION } from '../../src/generated/applicationProtocol.js';
import { describe, expect, it } from 'vitest';
import {
  type Clock,
  type WebSocketLike,
  ApplicationWebSocketClient,
  ConnectionLostError,
  ProtocolVersionMismatchError,
} from '../../src/application/ApplicationWebSocketClient.js';

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
    kind: 'web',
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

function hello(serverId = 'service-test'): ServerMessage {
  return {
    op: 'server.hello', protocol_version: APPLICATION_PROTOCOL_VERSION, server_id: serverId,
    queries: ['settings.get'], commands: ['settings.update'], subscriptions: ['projections'],
    terminal_streams: true, fact_cursor: 2, projection_cursor: 3,
  };
}

function reconnectHarness(): {
  client: ApplicationWebSocketClient;
  sockets: MockWebSocket[];
  delays: number[];
  releaseNextSleep(): void;
} {
  const sockets: MockWebSocket[] = [];
  const delays: number[] = [];
  const releases: Array<() => void> = [];
  const clock: Clock = {
    random: () => 0.5,
    sleep(ms) {
      delays.push(ms);
      let release!: () => void;
      return { promise: new Promise<void>((resolve) => { release = resolve; releases.push(resolve); }), cancel: () => release() };
    },
  };
  const client = new ApplicationWebSocketClient({
    url: 'ws://test/api/ws', clientId: 'reconnect-test', kind: 'tui', clock,
    backoff: { baseMs: 10, capMs: 20 }, webSocketFactory: () => {
      const socket = new MockWebSocket();
      sockets.push(socket);
      return socket;
    },
  });
  return { client, sockets, delays, releaseNextSleep: () => releases.shift()?.() };
}

async function reconnect(harness: ReturnType<typeof reconnectHarness>): Promise<MockWebSocket> {
  harness.releaseNextSleep();
  await flush();
  const socket = harness.sockets.at(-1);
  if (socket === undefined) throw new Error('missing reconnect socket');
  socket.open();
  socket.receive(hello());
  await flush();
  return socket;
}

function invalidation(cursor: number): ServerMessage {
  return {
    op: 'subscription.event', subscription_id: 'unused', cursor,
    payload: { type: 'projection.invalidate', projection: 'roster', subject_key: 'all', generation: 1, source_fact_id: null },
  } as ServerMessage;
}

describe('ApplicationWebSocketClient', () => {
  it('uses the sole application endpoint and closed hello frame', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    expect(client.getFactCursor()).toBe(2);
    expect(client.getProjectionCursor()).toBe(3);
    client.close();
  });

  it('treats a protocol mismatch as permanent', async () => {
    const { client, socket } = makeClient();
    const pending = client.connect();
    socket.open();
    socket.receive({
      op: 'server.hello', protocol_version: APPLICATION_PROTOCOL_VERSION + 1, server_id: 'other',
      queries: [], commands: [], subscriptions: [], terminal_streams: true, fact_cursor: 0, projection_cursor: 0,
    });
    await expect(pending).rejects.toBeInstanceOf(ProtocolVersionMismatchError);
    await expect(client.connect()).rejects.toBeInstanceOf(ProtocolVersionMismatchError);
    client.close();
  });

  it('accepts server.hello text frames delivered as Buffer', async () => {
    const { client, socket } = makeClient();
    const pending = client.connect();
    socket.open();
    const hello: ServerMessage = {
      op: 'server.hello',
      protocol_version: APPLICATION_PROTOCOL_VERSION,
      server_id: 'buffer-hello',
      queries: [],
      commands: [],
      subscriptions: [],
      terminal_streams: true,
      fact_cursor: 9,
      projection_cursor: 8,
    };
    socket.onmessage?.({ data: Buffer.from(JSON.stringify(hello), 'utf8') });
    await pending;
    expect(client.getFactCursor()).toBe(9);
    expect(client.getProjectionCursor()).toBe(8);
    client.close();
  });

  it('rejects a connection waiter when the socket closes during handshake', async () => {
    const { client, socket } = makeClient();
    const pending = client.connect();
    socket.close();
    await expect(pending).rejects.toBeInstanceOf(ConnectionLostError);
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

  it('does not send terminal.attach when sessionId is null', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const before = socket.sent.length;
    const detach = client.attachTerminal(null, () => {});
    expect(socket.sent.slice(before)).toEqual([]);
    detach();
    client.close();
  });

  it('fails a pending request promptly when its connection is lost', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const pending = client.query('settings.get', {});
    await flush();
    socket.close();
    await expect(pending).rejects.toBeInstanceOf(ConnectionLostError);
    client.close();
  });

  it('uses capped jittered reconnect backoff through the injected clock', async () => {
    const h = reconnectHarness();
    const pending = h.client.connect();
    h.sockets[0]?.close();
    await expect(pending).rejects.toBeInstanceOf(ConnectionLostError);
    expect(h.delays).toEqual([5]);
    h.releaseNextSleep(); await flush();
    h.sockets[1]?.close(); await flush();
    expect(h.delays).toEqual([5, 10]);
    h.releaseNextSleep(); await flush();
    h.sockets[2]?.close(); await flush();
    h.releaseNextSleep(); await flush();
    h.sockets[3]?.close(); await flush();
    expect(h.delays).toEqual([5, 10, 10, 10]);
    h.client.close();
  });

  it('buffers a live projection event that arrives before subscription.ready', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const seen: unknown[] = [];
    const hydration = client.hydrate('roster', (event) => seen.push(event), null);
    await flush();
    const subscription = socket.message(1);
    if (subscription.op !== 'subscribe') throw new Error('expected subscription');
    socket.receive({ ...invalidation(4), subscription_id: subscription.subscription_id } as unknown as ServerMessage);
    socket.receive({ op: 'subscription.ready', subscription_id: subscription.subscription_id,
      snapshot: { snapshots: {} as never, cursor: 3, mode: 'cold', replay: [] } });
    await hydration;
    expect(seen).toHaveLength(1);
    client.close();
  });

  it('deduplicates overlap between replay and the pre-ready projection buffer', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const seen: unknown[] = [];
    const hydration = client.hydrate('roster', (event) => seen.push(event), null);
    await flush();
    const subscription = socket.message(1);
    if (subscription.op !== 'subscribe') throw new Error('expected subscription');
    const event = { ...invalidation(4), subscription_id: subscription.subscription_id } as unknown as ServerMessage;
    socket.receive(event);
    socket.receive({ op: 'subscription.ready', subscription_id: subscription.subscription_id,
      snapshot: { snapshots: {} as never, cursor: 3, mode: 'resume', replay: [{ cursor: 4, payload: (event as { payload: Record<string, unknown> }).payload }] } });
    await hydration;
    expect(seen).toHaveLength(1);
    client.close();
  });

  it('resumes a subscription at its confirmed cursor after reconnect', async () => {
    const h = reconnectHarness();
    const initial = h.client.connect(); h.sockets[0]?.open(); h.sockets[0]?.receive(hello()); await initial;
    const hydration = h.client.hydrate('roster', () => {}, null); await flush();
    const subscription = h.sockets[0]?.message(1);
    if (subscription?.op !== 'subscribe') throw new Error('expected subscription');
    h.sockets[0]?.receive({ op: 'subscription.ready', subscription_id: subscription.subscription_id,
      snapshot: { snapshots: {} as never, cursor: 3, mode: 'cold', replay: [] } });
    await hydration;
    h.sockets[0]?.receive({ ...invalidation(4), subscription_id: subscription.subscription_id } as unknown as ServerMessage);
    h.sockets[0]?.close(); await flush();
    const next = await reconnect(h);
    expect(next.sent.map((raw) => JSON.parse(raw)).find((m) => m.op === 'subscribe')).toMatchObject({ subscription: { cursor: 4 } });
    h.client.close();
  });

  it('delivers reconnect snapshot fallback through the replacement callback', async () => {
    const h = reconnectHarness();
    const initial = h.client.connect(); h.sockets[0]?.open(); h.sockets[0]?.receive(hello()); await initial;
    const replacements: unknown[] = [];
    const hydration = h.client.hydrate('roster', undefined, null, (reply) => replacements.push(reply)); await flush();
    const subscription = h.sockets[0]?.message(1); if (subscription?.op !== 'subscribe') throw new Error('expected subscription');
    h.sockets[0]?.receive({ op: 'subscription.ready', subscription_id: subscription.subscription_id,
      snapshot: { snapshots: {} as never, cursor: 3, mode: 'cold', replay: [] } });
    await hydration;
    h.sockets[0]?.close(); await flush();
    const replacementSocket = await reconnect(h);
    const replacementSubscription = replacementSocket.message(1);
    if (replacementSubscription.op !== 'subscribe') throw new Error('expected re-subscription');
    expect(replacementSubscription.subscription_id).toBe(subscription.subscription_id);
    replacementSocket.receive({ op: 'subscription.ready', subscription_id: replacementSubscription.subscription_id,
      snapshot: { snapshots: {} as never, cursor: 5, mode: 'snapshot_fallback', replay: [] } });
    expect(replacements).toHaveLength(1);
    h.client.close();
  });

  it('does not resurrect an unsubscribe issued while reconnecting', async () => {
    const h = reconnectHarness();
    const initial = h.client.connect(); h.sockets[0]?.open(); h.sockets[0]?.receive(hello()); await initial;
    const hydration = h.client.hydrate('roster', () => {}, null); await flush();
    const sub = h.sockets[0]?.message(1); if (sub?.op !== 'subscribe') throw new Error('expected subscription');
    h.sockets[0]?.receive({ op: 'subscription.ready', subscription_id: sub.subscription_id,
      snapshot: { snapshots: {} as never, cursor: 3, mode: 'cold', replay: [] } });
    const ready = await hydration;
    h.sockets[0]?.close(); await flush(); ready.unsubscribe();
    const next = await reconnect(h);
    expect(next.sent.map((raw) => JSON.parse(raw)).some((m) => m.op === 'subscribe')).toBe(false);
    h.client.close();
  });

  it('detects terminal sequence gaps and requests a keyframe resync', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const updates: unknown[] = [];
    client.attachTerminal('session-1', (update) => updates.push(update));
    const attach = socket.message(1); if (attach.op !== 'terminal.attach') throw new Error('expected attach');
    socket.receive({ op: 'terminal.chunk', stream_id: attach.stream_id,
      chunk: { type: 'terminal.chunk', sequence: 2 } } as unknown as ServerMessage);
    expect(updates).toMatchObject([{ type: 'terminal.gap', expected_sequence: 1, next_sequence: 2 }]);
    expect(socket.message(2)).toMatchObject({ op: 'terminal.resync', after_sequence: 0, reason: 'gap' });
    client.close();
  });

  it('isolates connection listener exceptions from transport state', async () => {
    const { client, socket } = makeClient();
    client.onConnect(() => { throw new Error('consumer bug'); });
    await connect(client, socket);
    const pending = client.query('settings.get', {}); await flush();
    const request = socket.message(1); if (request.op !== 'request') throw new Error('expected request');
    socket.receive({ op: 'reply', request_id: request.request_id, result: { ok: true, settings: {} } });
    await expect(pending).resolves.toMatchObject({ ok: true });
    client.close();
  });

  it('isolates terminal renderer exceptions from sibling request dispatch', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    client.attachTerminal('session-1', () => { throw new Error('renderer bug'); });
    const attach = socket.message(1); if (attach.op !== 'terminal.attach') throw new Error('expected attach');
    socket.receive({ op: 'terminal.chunk', stream_id: attach.stream_id,
      chunk: { type: 'terminal.chunk', sequence: 1 } } as unknown as ServerMessage);
    const pending = client.query('settings.get', {}); await flush();
    const request = socket.message(2); if (request.op !== 'request') throw new Error('expected request');
    socket.receive({ op: 'reply', request_id: request.request_id, result: { ok: true, settings: {} } });
    await expect(pending).resolves.toMatchObject({ ok: true });
    client.close();
  });

  it('acquires, renews, and releases terminal input leases', async () => {
    const { client, socket } = makeClient();
    await connect(client, socket);
    const open = client.openTerminalInput('session-1'); await flush();
    const acquire = socket.message(1); if (acquire.op !== 'request') throw new Error('expected acquire');
    socket.receive({ op: 'reply', request_id: acquire.request_id,
      result: { lease: { lease_id: 'lease-1', fence: 1 } } } as ServerMessage);
    const lease = await open;
    const renew = client.renewTerminalInput(lease); await flush();
    const renewRequest = socket.message(2); if (renewRequest.op !== 'request') throw new Error('expected renew');
    socket.receive({ op: 'reply', request_id: renewRequest.request_id,
      result: { lease: { lease_id: 'lease-2', fence: 2 } } } as ServerMessage);
    const renewed = await renew;
    const close = client.closeTerminalInput(renewed); await flush();
    const release = socket.message(3); if (release.op !== 'request') throw new Error('expected release');
    socket.receive({ op: 'reply', request_id: release.request_id, result: { ok: true } } as ServerMessage);
    await close;
    expect(socket.message(4)).toEqual({ op: 'terminal.input_detach', stream_id: lease.streamId });
    client.close();
  });

  it('never replays one-way terminal input after reconnect', async () => {
    const h = reconnectHarness();
    const initial = h.client.connect(); h.sockets[0]?.open(); h.sockets[0]?.receive(hello()); await initial;
    expect(h.client.sendTerminalInput({ streamId: 'input-1', sessionId: 'session-1', leaseId: 'lease', fence: 1, inputSequence: 1, data: 'YQ==' })).toBe(true);
    h.sockets[0]?.close(); await flush();
    const next = await reconnect(h);
    expect(next.sent.map((raw) => JSON.parse(raw)).some((message) => message.op === 'terminal.input')).toBe(false);
    h.client.close();
  });

  it('stops reconnecting after an intentional close', async () => {
    const h = reconnectHarness();
    const initial = h.client.connect(); h.sockets[0]?.open(); h.sockets[0]?.receive(hello()); await initial;
    h.sockets[0]?.close(); await flush();
    expect(h.delays).toHaveLength(1);
    h.client.close(); h.releaseNextSleep(); await flush();
    expect(h.sockets).toHaveLength(1);
  });
});
