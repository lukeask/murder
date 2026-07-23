/**
 * Responsive layout switch: the App renders a three-region desktop tree above the mobile breakpoint
 * and a single-pane + tab-bar tree below it. We stub `window.matchMedia` to force each side and
 * assert the structural difference (the one thing CSS alone can't express). Drives a real store via
 * a FakeApplicationClient; the App's onConnect re-prime is exercised through a minimal fake.
 */

import { AppStoreProvider } from '@core/hooks/useAppStore.js';
import { createAppStore } from '@core/store/store.js';
import { FakeApplicationClient } from '@core/application/FakeApplicationClient.js';
import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App.js';
import { ApplicationClientProvider } from '../src/application/ApplicationClientContext.js';
import type { ApplicationWebSocketClient } from '../src/application/ApplicationWebSocketClient.js';
import { MOBILE_QUERY } from '../src/useMediaQuery.js';

function stubMatchMedia(isMobile: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === MOBILE_QUERY ? isMobile : false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
}

/** A FakeApplicationClient extended with the connection-status callbacks the App's header needs (no-ops). */
function fakeBus(): ApplicationWebSocketClient {
  const bus = new FakeApplicationClient() as unknown as Record<string, unknown>;
  bus['onConnect'] = () => () => {};
  bus['onDisconnect'] = () => () => {};
  bus['onPermanentError'] = () => () => {};
  return bus as unknown as ApplicationWebSocketClient;
}

function renderApp(): HTMLElement {
  const bus = fakeBus();
  const { store } = createAppStore(bus as unknown as FakeApplicationClient);
  const { container } = render(
    <AppStoreProvider value={store}>
      <ApplicationClientProvider value={bus}>
        <App bus={bus} />
      </ApplicationClientProvider>
    </AppStoreProvider>,
  );
  return container;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('responsive layout', () => {
  it('renders the three-region desktop cockpit above the breakpoint', () => {
    stubMatchMedia(false);
    const container = renderApp();
    expect(container.querySelector('.app')?.getAttribute('data-layout')).toBe('desktop');
    // The cockpit grid: NavBar + 3-rail body + KeybindBar. Two scrolling rails (left + right).
    expect(container.querySelector('.cockpit')).not.toBeNull();
    expect(container.querySelector('.cockpit__cols')).not.toBeNull();
    expect(container.querySelectorAll('.rail')).toHaveLength(2);
    // No mobile tab bar on desktop.
    expect(container.querySelector('.tabbar')).toBeNull();
  });

  it('renders the single-pane + pill tab-bar mobile layout below the breakpoint', () => {
    stubMatchMedia(true);
    const container = renderApp();
    expect(container.querySelector('.app')?.getAttribute('data-layout')).toBe('mobile');
    expect(container.querySelector('.app__body--mobile')).not.toBeNull();
    // No desktop rails; a DS pill Tabs switcher instead (each tab a `.mds-tab` button).
    expect(container.querySelectorAll('.rail')).toHaveLength(0);
    expect(container.querySelector('.tabbar')).not.toBeNull();
    expect(container.querySelector('.mds-tabs--pill')).not.toBeNull();
    expect(container.querySelectorAll('.mds-tab').length).toBeGreaterThan(1);
  });
});
