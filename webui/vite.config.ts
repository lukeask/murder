/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Develop and bundle against the workspace source so changes in ui-core are immediately visible.
const coreSrc = fileURLToPath(new URL('../ui-core/src', import.meta.url));


/**
 * Dev proxy target for `/api/ws` → the live murder service WebSocket.
 *
 * There is no unix-socket / bus bridge. The service serves `/api/ws` itself; `murder web up`
 * prints the browser base URL (e.g. `http://127.0.0.1:NNNN`). Point the proxy at that host/port:
 *
 *   # Preferred: full application WS URL (same shape as the session registry's websocket_url)
 *   export VITE_APPLICATION_WS_URL=ws://127.0.0.1:NNNN/api/ws
 *
 *   # Or: explicit proxy origin only
 *   export VITE_APPLICATION_WS_PROXY=ws://127.0.0.1:NNNN
 *
 *   # Deprecated alias (still honoured): port-only override from the old bus-bridge era
 *   export VITE_BUS_PROXY_PORT=NNNN
 *
 * If none of these are set, the `/api/ws` proxy is omitted — set one from `murder web up` before
 * `npm run dev` when you need a live service. Production builds are same-origin (no proxy).
 */
function applicationWsProxyTarget(): string | undefined {
  const explicit = process.env['VITE_APPLICATION_WS_PROXY'];
  if (explicit !== undefined && explicit !== '') return explicit;

  const wsUrl = process.env['VITE_APPLICATION_WS_URL'];
  if (wsUrl !== undefined && wsUrl !== '') {
    try {
      const parsed = new URL(wsUrl);
      return `${parsed.protocol}//${parsed.host}`;
    } catch {
      // Fall through — invalid URL is treated as unset.
    }
  }

  const legacyPort = process.env['VITE_BUS_PROXY_PORT'];
  if (legacyPort !== undefined && legacyPort !== '') {
    return `ws://localhost:${legacyPort}`;
  }

  return undefined;
}

function applicationWsProxy() {
  const target = applicationWsProxyTarget();
  if (target === undefined) return {};
  return {
    '/api/ws': {
      target,
      ws: true,
      changeOrigin: true,
    },
  };
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: '@murder/ui-core', replacement: coreSrc },
    ],
  },
  server: {
    fs: {
      // Allow Vite to read the workspace core source outside the webui root.
      allow: ['..'],
    },
    proxy: applicationWsProxy(),
  },
  build: {
    // Shipped by the Python service as `murder/_webui/`. Keep this path stable — the packaging step
    // copies `webui/dist` wholesale.
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    globals: false,
  },
});
