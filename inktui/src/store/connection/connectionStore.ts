/**
 * `connectionStore` — the live transport-connection fact: is the bus socket connected, reconnecting,
 * or permanently broken (a protocol-version mismatch)?
 *
 * This is the single source of truth for the {@link ../../components/TopBar.js TopBar}'s connection
 * badge. Like {@link ../../terminal/capsStore.js capsStore} (and unlike an app-store slice) it is
 * written by the **transport wiring** in `index.tsx` — the `connect()`/`onConnect`/`onDisconnect`/
 * `onPermanentError` hooks of the {@link ../../bus/UdsBusClient.js UdsBusClient} — not by any
 * bus-RPC action. Connection state is a process-wide transport fact, not entity state pulled from
 * the service, so it lives outside the slice graph exactly as terminal capability does.
 *
 * Framework-agnostic vanilla Zustand, mirroring {@link ../../terminal/capsStore.js capsStore}: a
 * React hook ({@link useConnectionStatus}) wraps it for the TopBar, while the wiring in `index.tsx`
 * and unit tests drive it directly. Two exports plus the hook, exactly like capsStore:
 *  - {@link createConnectionStore} — the factory (a fresh, isolated instance per unit test);
 *  - {@link connectionStore} — the process-global singleton production imports;
 *  - {@link useConnectionStatus} — the React binding the TopBar reads.
 */

import { useStoreWithEqualityFn } from 'zustand/traditional';
import { createStore, type StoreApi } from 'zustand/vanilla';

/**
 * The transport-connection state.
 *  - `'unknown'` — initial; the wiring has not reported anything yet. Renders NO badge, which is
 *    exactly what smoke runs, the fake bus, and most component tests want: a transport that never
 *    drives this store (the {@link ../../bus/FakeBusClient.js FakeBusClient} has no connect hooks)
 *    leaves the badge silent rather than asserting a misleading "connected"/"connecting".
 *  - `'connecting'` — the live wiring set this just before the first `connect()`.
 *  - `'connected'` — a handshake completed (`onConnect`).
 *  - `'reconnecting'` — an established connection dropped and backoff is retrying (`onDisconnect`).
 *  - `'version-mismatch'` — the client gave up permanently on a protocol-version mismatch
 *    (`onPermanentError`); the user must restart murder.
 */
export type ConnectionStatus =
  | 'unknown'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'version-mismatch';

/** The connection store's state + its single setter. */
export interface ConnectionState {
  /** The live transport-connection status; `'unknown'` until the wiring reports. */
  readonly status: ConnectionStatus;
  /** Record the current transport status. Called only by the `index.tsx` transport wiring. */
  setStatus(status: ConnectionStatus): void;
}

/** The handle type, re-exported so callers don't import `zustand/vanilla`. */
export type ConnectionStoreApi = StoreApi<ConnectionState>;

/** Create a connection store. Starts in `'unknown'` (or a seeded value, for tests). Each call is an
 * independent instance — unit tests build a fresh one per case for isolation. */
export function createConnectionStore(initial: ConnectionStatus = 'unknown'): ConnectionStoreApi {
  return createStore<ConnectionState>()((set) => ({
    status: initial,
    setStatus(status) {
      set({ status });
    },
  }));
}

/**
 * The process-global connection store. Like {@link ../../terminal/capsStore.js capsStore} this is a
 * module-level singleton: transport-connection state is a single process-wide fact (one bus socket),
 * written by the wiring in `index.tsx` and read — via {@link useConnectionStatus} — by the TopBar's
 * connection badge. A test seeds it directly with {@link ConnectionState.setStatus} and resets it to
 * `'unknown'` between cases.
 */
export const connectionStore: ConnectionStoreApi = createConnectionStore();

/** The live connection status. Re-renders the calling component whenever the transport status
 * changes (so the TopBar badge appears/updates the moment the wiring reports a drop or mismatch). */
export function useConnectionStatus(): ConnectionStatus {
  return useStoreWithEqualityFn(connectionStore, (s) => s.status);
}
