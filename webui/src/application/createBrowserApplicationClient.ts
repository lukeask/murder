/** Browser-specific construction for the shared application WebSocket client. */

import {
  ApplicationWebSocketClient,
  type ApplicationLogger,
  type BackoffConfig,
  type Clock,
  type WebSocketFactory,
  type WebSocketLike,
} from '@murder/ui-core/application/ApplicationWebSocketClient.js';

export interface BrowserLocationLike {
  readonly protocol: string;
  readonly host: string;
  /** Query string including leading `?`, used for `/?repo={id}` deep links. */
  readonly search?: string;
}

export interface BrowserStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface CreateBrowserApplicationClientOptions {
  url?: string;
  /** Repository partition for `/api/ws/{repository_id}` and per-repo client_id storage. */
  repositoryId?: string;
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

/** localStorage key for the stable web client_id of one repository. */
export function clientIdStorageKey(repositoryId: string): string {
  return `murder.web.${repositoryId}.client_id`;
}

/** Read `/?repo={id}` from `location.search` (deep-link before same-origin host default). */
export function repositoryIdFromLocation(location: BrowserLocationLike): string | null {
  const raw = location.search ?? '';
  if (raw === '') return null;
  const params = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : raw);
  const repo = params.get('repo');
  if (repo === null) return null;
  const trimmed = repo.trim();
  return trimmed === '' ? null : trimmed;
}

/**
 * Same-origin application WebSocket URL for a repository.
 *
 * Prefer an explicit `repositoryId`; otherwise read `/?repo={id}` from `location.search`,
 * then build `{ws|wss}://{location.host}/api/ws/{repository_id}`.
 */
export function defaultApplicationWebSocketUrl(
  location: BrowserLocationLike = globalThis.location,
  repositoryId?: string,
): string {
  const id = repositoryId ?? repositoryIdFromLocation(location);
  if (id === null || id === '') {
    throw new Error('repository_id required for application WebSocket URL (pass repositoryId or ?repo=)');
  }
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/api/ws/${encodeURIComponent(id)}`;
}

export function createBrowserApplicationClient(
  options: CreateBrowserApplicationClientOptions = {},
): ApplicationWebSocketClient {
  const location = options.location ?? globalThis.location;
  const repositoryId = options.repositoryId ?? repositoryIdFromLocation(location);
  const idFactory = options.idFactory ?? browserId;
  const clientId =
    options.clientId ??
    (repositoryId !== null
      ? stableClientId(options.storage, idFactory, repositoryId)
      : `web-${idFactory()}`);
  const makeSocket = options.webSocketFactory ?? ((url: string) =>
    new WebSocket(url) as unknown as WebSocketLike);
  const url =
    options.url ??
    defaultApplicationWebSocketUrl(location, repositoryId ?? undefined);
  return new ApplicationWebSocketClient({
    url,
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

function stableClientId(
  storage: BrowserStorageLike | undefined,
  idFactory: () => string,
  repositoryId: string,
): string {
  try {
    const resolvedStorage = storage ?? globalThis.localStorage;
    const key = clientIdStorageKey(repositoryId);
    const stored = resolvedStorage.getItem(key);
    if (stored !== null && stored !== '') return stored;
    const created = `web-${idFactory()}`;
    resolvedStorage.setItem(key, created);
    return created;
  } catch {
    return `web-${idFactory()}`;
  }
}

function browserId(): string {
  return globalThis.crypto.randomUUID();
}
