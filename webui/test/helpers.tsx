/**
 * Test helpers: build a real app store driven by a {@link FakeApplicationClient}, and a render wrapper that
 * mounts the store + bus providers so a panel/component renders off live slice state. Slices are
 * seeded by writing directly to the vanilla store (the store is framework-agnostic) — the rendering
 * is what these component tests exercise, not the RPC plumbing (that's covered by the core slices).
 */

import { AppStoreProvider } from '@core/hooks/useAppStore.js';
import { createAppStore } from '@core/store/store.js';
import type { AppStore, AppStoreApi } from '@core/store/store.js';
import { FakeApplicationClient } from '@core/application/FakeApplicationClient.js';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ApplicationClientProvider } from '../src/application/ApplicationClientContext.js';
import type { ApplicationWebSocketClient } from '../src/application/ApplicationWebSocketClient.js';

export function makeStore(): { store: AppStoreApi; bus: FakeApplicationClient } {
  const bus = new FakeApplicationClient();
  const { store } = createAppStore(bus);
  return { store, bus };
}

/** Render `ui` inside the store + bus providers. The FakeApplicationClient is cast to the ApplicationWebSocketClient shape
 * the ApplicationClientProvider expects — components that subscribe via `useApplicationClient` use only the base `subscribe`. */
export function renderWithStore(
  ui: ReactNode,
  opts?: { store?: AppStoreApi; bus?: FakeApplicationClient },
): { store: AppStoreApi; bus: FakeApplicationClient } {
  const bus = opts?.bus ?? new FakeApplicationClient();
  const store = opts?.store ?? createAppStore(bus).store;
  render(
    <AppStoreProvider value={store}>
      <ApplicationClientProvider value={bus as unknown as ApplicationWebSocketClient}>{ui}</ApplicationClientProvider>
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
