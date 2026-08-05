/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Develop and bundle against the workspace source so changes in ui-core are immediately visible.
const coreSrc = fileURLToPath(new URL('../ui-core/src', import.meta.url));


/** Hard-coded single-daemon listener (`murder/app/protocol/common.py`). */
const DEFAULT_DAEMON_WS_PROXY = 'ws://127.0.0.1:62077';

/**
 * Dev proxy target for `/api/*` → the live murder daemon (HTTP picker + WebSocket).
 *
 * There is no unix-socket / bus bridge. The daemon serves `/api/repos` and
 * `/api/ws/{repository_id}` itself on {@link DEFAULT_DAEMON_WS_PROXY}. Overrides:
 *
 *   # Preferred: full application WS URL (path segment optional; host/port extracted)
 *   export VITE_APPLICATION_WS_URL=ws://127.0.0.1:62077/api/ws
 *
 *   # Or: explicit proxy origin only
 *   export VITE_APPLICATION_WS_PROXY=ws://127.0.0.1:62077
 *
 *   # Deprecated alias (still honoured): port-only override from the old bus-bridge era
 *   export VITE_BUS_PROXY_PORT=NNNN
 *
 * When none are set, proxy defaults to `:62077` so `npm run dev` reaches the daemon.
 * Production builds are same-origin (no proxy).
 */
function applicationWsProxyTarget(): string {
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

  return DEFAULT_DAEMON_WS_PROXY;
}

function applicationApiProxy() {
  const target = applicationWsProxyTarget();
  // Vite HTTP proxy wants http(s); WS upgrades still work with `ws: true`.
  const httpTarget = target.replace(/^ws/i, 'http');
  return {
    '/api': {
      target: httpTarget,
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
    proxy: applicationApiProxy(),
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
