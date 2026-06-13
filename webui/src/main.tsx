/**
 * Web entrypoint. Wires the data spine once: construct the WebSocket bus client, build the shared
 * store against it (`@core/store`), mount the store provider, connect, and render {@link App}.
 *
 * This is the web mirror of inktui's `src/index.tsx` — same store, same provider, different
 * transport (WS instead of unix socket) and different renderer (DOM instead of Ink).
 */

import { AppStoreProvider } from '@core/hooks/useAppStore.js';
import { createAppStore } from '@core/store/store.js';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App.js';
import { BusProvider } from './bus/BusContext.js';
import { WsBusClient } from './bus/WsBusClient.js';
import './styles/theme.css';
import './styles/app.css';

const bus = new WsBusClient({ logger: console });
const { store } = createAppStore(bus);

// Kick the connection. RPC/subscribe also lazily connect, but starting here means the header shows
// "connecting…" immediately and the store's bus subscriptions are live before first paint.
void bus.connect().catch((error: unknown) => {
  // A permanent (protocol-version-mismatch) rejection is surfaced via onPermanentError → the
  // header's "version mismatch" state; nothing else to do here but avoid an unhandled rejection.
  console.warn('initial bus connect failed:', error);
});

const rootEl = document.getElementById('root');
if (rootEl === null) {
  throw new Error('missing #root element');
}

createRoot(rootEl).render(
  <StrictMode>
    <AppStoreProvider value={store}>
      <BusProvider value={bus}>
        <App bus={bus} />
      </BusProvider>
    </AppStoreProvider>
  </StrictMode>,
);
