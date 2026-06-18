/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Absolute path to the portable inktui core (sibling dir). The web app imports the store, bus
// protocol, selectors and theme straight off this tree via the `@core` alias — the whole point of
// the "easy port": one source of truth for the data spine, no fork.
const coreSrc = fileURLToPath(new URL('../inktui/src', import.meta.url));

// Webui's own React copy. `resolve.dedupe` handles the dev/prod bundle, but Vitest's transform
// pipeline can still resolve a SECOND React through the out-of-root inktui tree
// (`inktui/node_modules/react`), which nulls hooks ("Cannot read properties of null (reading
// 'useRef')") in component tests. Pinning explicit aliases to these single physical copies forces
// one instance everywhere — build, dev, and test.
const reactDir = fileURLToPath(new URL('./node_modules/react', import.meta.url));
const reactDomDir = fileURLToPath(new URL('./node_modules/react-dom', import.meta.url));
// zustand + use-sync-external-store sit between the core's hooks and React. Pinned to webui's copies
// (subpaths like `zustand/shallow` resolve under the dir) so an import originating in the inktui
// tree can't drag a second React through `inktui/node_modules`.
const zustandDir = fileURLToPath(new URL('./node_modules/zustand', import.meta.url));
const useSyncDir = fileURLToPath(new URL('./node_modules/use-sync-external-store', import.meta.url));

// Dev-time bus bridge target. `murder web up -f` runs the WS<->unix-socket relay on this port; the
// dev server proxies `/bus` to it so `npm run dev` talks to a locally-running supervisor. In a
// production build the bridge serves `webui/dist` itself, so `/bus` is same-origin and no proxy is
// needed. Override the port with VITE_BUS_PROXY_PORT if your bridge binds elsewhere.
const busProxyPort = process.env['VITE_BUS_PROXY_PORT'] ?? '8473';

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Order matters: more specific (`react-dom`) before its prefix (`react`). Each maps the bare
    // package + its subpaths to webui's single physical copy.
    alias: [
      { find: '@core', replacement: coreSrc },
      { find: /^react-dom$/, replacement: reactDomDir },
      { find: /^react-dom\//, replacement: `${reactDomDir}/` },
      { find: /^react$/, replacement: reactDir },
      { find: /^react\//, replacement: `${reactDir}/` },
      { find: /^zustand$/, replacement: zustandDir },
      { find: /^zustand\//, replacement: `${zustandDir}/` },
      { find: /^use-sync-external-store$/, replacement: useSyncDir },
      { find: /^use-sync-external-store\//, replacement: `${useSyncDir}/` },
    ],
    // The web app and the aliased core both import React. Without dedupe, Vite can pull a second
    // React copy through the out-of-root core path, which breaks hooks ("invalid hook call"). Force
    // a single instance. Versions are aligned (react@19.2 in both package.json files) so this is
    // a single physical copy from webui/node_modules.
    dedupe: ['react', 'react-dom'],
  },
  server: {
    fs: {
      // Allow Vite to read the sibling inktui tree (outside webui root) so `@core/*` TS resolves
      // and is transpiled on demand.
      allow: ['..'],
    },
    proxy: {
      '/bus': {
        target: `ws://localhost:${busProxyPort}`,
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    // Shipped by the Python bridge as `murder/_webui/`. Keep this path stable — the packaging step
    // copies `webui/dist` wholesale.
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    globals: false,
    server: {
      deps: {
        // Process the out-of-root `@core` (inktui) tree + its React-binding deps through Vitest's
        // own module graph so they share webui's single React instance (honouring `resolve.dedupe`
        // / the react aliases above). Without this, the externalized inktui tree resolves a second
        // React through `inktui/node_modules`, nulling hooks in component tests.
        inline: [/inktui/, 'zustand', 'use-sync-external-store'],
      },
    },
  },
});
