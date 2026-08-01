/** TUI-specific construction for the shared application WebSocket client. */

import { randomUUID } from 'node:crypto';
import {
  ApplicationWebSocketClient,
  type ApplicationLogger,
  type BackoffConfig,
  type Clock,
  type WebSocketFactory,
  type WebSocketLike,
} from '@murder/ui-core/application/ApplicationWebSocketClient.js';

export interface CreateTuiApplicationClientOptions {
  readonly url: string;
  clientId?: string;
  webSocketFactory?: WebSocketFactory;
  logger?: ApplicationLogger;
  clock?: Clock;
  backoff?: BackoffConfig;
  requestTimeoutMs?: number;
  requestIdFactory?: () => string;
}

export function createTuiApplicationClient(
  options: CreateTuiApplicationClientOptions,
): ApplicationWebSocketClient {
  const makeSocket = options.webSocketFactory ?? ((url: string) => {
    const Factory = (globalThis as unknown as {
      WebSocket?: new (address: string) => WebSocketLike;
    }).WebSocket;
    if (Factory === undefined) throw new Error('this Node runtime has no WebSocket implementation');
    return new Factory(url);
  });
  return new ApplicationWebSocketClient({
    url: options.url,
    clientId: options.clientId ?? `tui-${randomUUID()}`,
    kind: 'tui',
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
