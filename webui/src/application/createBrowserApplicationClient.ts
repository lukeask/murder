/** Browser-specific construction for the shared application WebSocket client. */

import {
  ApplicationWebSocketClient,
  type ApplicationLogger,
  type BackoffConfig,
  type Clock,
  type WebSocketFactory,
  type WebSocketLike,
} from '@murder/ui-core/application/ApplicationWebSocketClient.js';

const CLIENT_ID_STORAGE_KEY = 'murder.web.client_id';

export interface BrowserLocationLike {
  readonly protocol: string;
  readonly host: string;
}

export interface BrowserStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface CreateBrowserApplicationClientOptions {
  url?: string;
  clientId?: string;
  location?: BrowserLocationLike;
  storage?: BrowserStorageLike;
  webSocketFactory?: WebSocketFactory;
  logger?: ApplicationLogger;
  clock?: Clock;
  backoff?: BackoffConfig;
  requestTimeoutMs?: number;
  requestIdFactory?: () => string;
  idFactory?: () => string;
}

export function defaultApplicationWebSocketUrl(location: BrowserLocationLike = globalThis.location): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/api/ws`;
}

export function createBrowserApplicationClient(
  options: CreateBrowserApplicationClientOptions = {},
): ApplicationWebSocketClient {
  const idFactory = options.idFactory ?? browserId;
  const clientId = options.clientId ?? stableClientId(options.storage, idFactory);
  const makeSocket = options.webSocketFactory ?? ((url: string) =>
    new WebSocket(url) as unknown as WebSocketLike);
  return new ApplicationWebSocketClient({
    url: options.url ?? defaultApplicationWebSocketUrl(options.location),
    clientId,
    kind: 'web',
    webSocketFactory: makeSocket,
    logger: options.logger ?? console,
    ...(options.clock === undefined ? {} : { clock: options.clock }),
    ...(options.backoff === undefined ? {} : { backoff: options.backoff }),
    ...(options.requestTimeoutMs === undefined
      ? {}
      : { requestTimeoutMs: options.requestTimeoutMs }),
    ...(options.requestIdFactory === undefined
      ? {}
      : { requestIdFactory: options.requestIdFactory }),
  });
}

function stableClientId(storage: BrowserStorageLike | undefined, idFactory: () => string): string {
  try {
    const resolvedStorage = storage ?? globalThis.localStorage;
    const stored = resolvedStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (stored !== null && stored !== '') return stored;
    const created = `web-${idFactory()}`;
    resolvedStorage.setItem(CLIENT_ID_STORAGE_KEY, created);
    return created;
  } catch {
    return `web-${idFactory()}`;
  }
}

function browserId(): string {
  return globalThis.crypto.randomUUID();
}
