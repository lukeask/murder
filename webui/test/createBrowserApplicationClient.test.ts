import { APPLICATION_PROTOCOL_VERSION, type ServerMessage } from '@murder/ui-core/generated/applicationProtocol.js';
import type { WebSocketLike } from '@murder/ui-core/application/ApplicationWebSocketClient.js';
import { expect, it } from 'vitest';
import { createBrowserApplicationClient } from '../src/application/createBrowserApplicationClient.js';

class Socket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  send(data: string): void { this.sent.push(data); }
  close(): void { this.readyState = 3; this.onclose?.({}); }
  open(): void { this.readyState = 1; this.onopen?.({}); }
  hello(): void { this.onmessage?.({ data: JSON.stringify({ op: 'server.hello', protocol_version: APPLICATION_PROTOCOL_VERSION, server_id: 'test', queries: [], commands: [], subscriptions: [], terminal_streams: true, fact_cursor: 0, projection_cursor: 0 } satisfies ServerMessage) }); }
}

it('browser factory derives a secure URL, persists its ID, and supplies kind web', async () => {
  const values = new Map<string, string>();
  let url = '';
  const socket = new Socket();
  const client = createBrowserApplicationClient({
    location: { protocol: 'https:', host: 'murder.test' },
    storage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) },
    idFactory: () => 'stable', webSocketFactory: (address) => { url = address; return socket; },
  });
  const connected = client.connect(); socket.open(); socket.hello(); await connected;
  expect(url).toBe('wss://murder.test/api/ws');
  expect(values.get('murder.web.client_id')).toBe('web-stable');
  expect(JSON.parse(socket.sent[0] ?? '')).toMatchObject({ client: { client_id: 'web-stable', kind: 'web' } });
  client.close();
});
