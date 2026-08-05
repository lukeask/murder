import { APPLICATION_PROTOCOL_VERSION, type ServerMessage } from '@murder/ui-core/generated/applicationProtocol.js';
import type { WebSocketLike } from '@murder/ui-core/application/ApplicationWebSocketClient.js';
import { expect, it } from 'vitest';
import {
  clientIdStorageKey,
  createBrowserApplicationClient,
  defaultApplicationWebSocketUrl,
  repositoryIdFromLocation,
} from '../src/application/createBrowserApplicationClient.js';

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

it('browser factory derives a path-scoped URL, persists per-repo ID, and supplies kind web', async () => {
  const values = new Map<string, string>();
  let url = '';
  const socket = new Socket();
  const client = createBrowserApplicationClient({
    repositoryId: 'repo-alpha',
    location: { protocol: 'https:', host: 'murder.test', search: '' },
    storage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) },
    idFactory: () => 'stable', webSocketFactory: (address) => { url = address; return socket; },
  });
  const connected = client.connect(); socket.open(); socket.hello(); await connected;
  expect(url).toBe('wss://murder.test/api/ws/repo-alpha');
  expect(values.get(clientIdStorageKey('repo-alpha'))).toBe('web-stable');
  expect(values.get('murder.web.client_id')).toBeUndefined();
  expect(JSON.parse(socket.sent[0] ?? '')).toMatchObject({ client: { client_id: 'web-stable', kind: 'web' } });
  client.close();
});

it('defaultApplicationWebSocketUrl reads ?repo= from location.search before host default', () => {
  expect(
    defaultApplicationWebSocketUrl({
      protocol: 'http:',
      host: '127.0.0.1:62077',
      search: '?repo=deep-link-id',
    }),
  ).toBe('ws://127.0.0.1:62077/api/ws/deep-link-id');
});

it('repositoryIdFromLocation parses and rejects empty repo params', () => {
  expect(repositoryIdFromLocation({ protocol: 'http:', host: 'x', search: '?repo=abc' })).toBe('abc');
  expect(repositoryIdFromLocation({ protocol: 'http:', host: 'x', search: '?repo=%20' })).toBeNull();
  expect(repositoryIdFromLocation({ protocol: 'http:', host: 'x', search: '' })).toBeNull();
});

it('namespaces client_id storage per repository without clobbering peers', async () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
  const mk = (repositoryId: string, id: string) => {
    const socket = new Socket();
    return createBrowserApplicationClient({
      repositoryId,
      location: { protocol: 'http:', host: 'localhost', search: '' },
      storage,
      idFactory: () => id,
      webSocketFactory: () => socket,
    });
  };
  const a = mk('repo-a', 'aaa');
  const b = mk('repo-b', 'bbb');
  expect(values.get(clientIdStorageKey('repo-a'))).toBe('web-aaa');
  expect(values.get(clientIdStorageKey('repo-b'))).toBe('web-bbb');
  const a2 = mk('repo-a', 'should-not-run');
  expect(values.get(clientIdStorageKey('repo-a'))).toBe('web-aaa');
  a.close();
  b.close();
  a2.close();
});
