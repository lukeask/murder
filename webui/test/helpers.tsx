/**
 * Test helpers: build a real app store driven by a {@link FakeBusClient}, and a render wrapper that
 * mounts the store + bus providers so a panel/component renders off live slice state. Slices are
 * seeded by writing directly to the vanilla store (the store is framework-agnostic) — the rendering
 * is what these component tests exercise, not the RPC plumbing (that's covered by the core slices).
 */

import { AppStoreProvider } from '@core/hooks/useAppStore.js';
import { createAppStore } from '@core/store/store.js';
import type { AppStore, AppStoreApi } from '@core/store/store.js';
import { FakeBusClient } from '@core/bus/FakeBusClient.js';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { BusProvider } from '../src/bus/BusContext.js';
import type { WsBusClient } from '../src/bus/WsBusClient.js';

export function makeStore(): { store: AppStoreApi; bus: FakeBusClient } {
  const bus = new FakeBusClient();
  const { store } = createAppStore(bus);
  return { store, bus };
}

/** Render `ui` inside the store + bus providers. The FakeBusClient is cast to the WsBusClient shape
 * the BusProvider expects — components that subscribe via `useBus` use only the base `subscribe`. */
export function renderWithStore(
  ui: ReactNode,
  opts?: { store?: AppStoreApi; bus?: FakeBusClient },
): { store: AppStoreApi; bus: FakeBusClient } {
  const bus = opts?.bus ?? new FakeBusClient();
  const store = opts?.store ?? createAppStore(bus).store;
  render(
    <AppStoreProvider value={store}>
      <BusProvider value={bus as unknown as WsBusClient}>{ui}</BusProvider>
    </AppStoreProvider>,
  );
  return { store, bus };
}

/** Overwrite one slice's state for rendering (a ready list with rows). */
export function seedSlice<K extends keyof AppStore>(
  store: AppStoreApi,
  key: K,
  value: AppStore[K],
): void {
  store.setState({ [key]: value } as unknown as Partial<AppStore>);
}
