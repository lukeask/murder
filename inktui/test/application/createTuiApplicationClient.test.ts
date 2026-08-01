import { APPLICATION_PROTOCOL_VERSION, type ServerMessage } from '@murder/ui-core/generated/applicationProtocol.js';
import type { WebSocketLike } from '@murder/ui-core/application/ApplicationWebSocketClient.js';
import { expect, it } from 'vitest';
import { createTuiApplicationClient } from '../../src/application/createTuiApplicationClient.js';

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

it('TUI factory keeps the supplied URL and identifies itself as tui', async () => {
  const socket = new Socket(); let url = '';
  const client = createTuiApplicationClient({ url: 'wss://service.test/api/ws', clientId: 'tui-test', webSocketFactory: (address) => { url = address; return socket; } });
  const connected = client.connect(); socket.open(); socket.hello(); await connected;
  expect(url).toBe('wss://service.test/api/ws');
  expect(JSON.parse(socket.sent[0] ?? '')).toMatchObject({ client: { client_id: 'tui-test', kind: 'tui' } });
  client.close();
});
