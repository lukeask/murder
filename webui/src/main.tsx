/**
 * Web entrypoint. Wires the data spine once: construct the WebSocket bus client, build the shared
 * store against it (`@core/store`), mount the store provider, connect, and render {@link App}.
 *
 * This is the web mirror of inktui's `src/index.tsx` — same store, same provider, different
 * transport (the shared application WebSocket) and different renderer (DOM instead of Ink).
 */

import { AppStoreProvider } from '@core/hooks/useAppStore.js';
import { createAppStore } from '@core/store/store.js';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App.js';
import { ApplicationClientProvider } from './application/ApplicationClientContext.js';
import { ApplicationWebSocketClient } from './application/ApplicationWebSocketClient.js';
import './styles/theme.css';
// Design-system token foundation: imported AFTER theme.css (so DS values win on overlapping names
// like --space-*/--radius/--font-mono) and BEFORE app.css (so the existing app chrome still reads
// the runtime --color-* vars unchanged). ds.css holds the ported .mds-* component rules.
import './styles/tokens.css';
import './styles/ds.css';
import './styles/ds-forms.css';
import './styles/ds-data.css';
import './styles/ds-navigation.css';
import './styles/ds-feedback.css';
import './styles/app.css';
// Cockpit shell layout (the DS reskin frame) + panel CSS. Imported AFTER the ds-*.css component
// sheets (so `.mds-*` rules exist) and after app.css (so the new `.cockpit*`/`.mw-*`/`.ticket-meta*`
// shell + panel rules win where intent overlaps). Later C2 panel groups each add their own
// `panels-<group>.css` import here during integration.
import './styles/cockpit.css';
import './styles/panels.css';
import './styles/panels-roster.css';
import './styles/panels-history.css';
import './styles/panels-docs.css';
import './styles/panels-usage.css';
import './styles/panels-transit.css';
import './styles/panels-settings.css';
import './styles/panels-stage.css';

const bus = new ApplicationWebSocketClient({ logger: console });
const { store } = createAppStore(bus);

// Kick the connection. Requests/streams also lazily connect, but starting here means the header shows
// "connecting…" immediately and the store's bus subscriptions are live before first paint.
void bus.connect().catch((error: unknown) => {
  // A permanent application-version mismatch is surfaced via onPermanentError → the
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
      <ApplicationClientProvider value={bus}>
        <App bus={bus} />
      </ApplicationClientProvider>
    </AppStoreProvider>
  </StrictMode>,
);
