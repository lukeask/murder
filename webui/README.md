# webui — web/mobile frontend for murder

A Vite + React 19 + TypeScript app that ports the Ink TUI to the browser. It **reuses the
framework-agnostic core** of `inktui/` (store, selectors, theme, wire protocol) verbatim and only
reimplements the parts that are terminal-specific: the transport (WebSocket instead of a Unix
socket) and the renderer (DOM instead of Ink).

The UI is a **cockpit**: header, stage (chat + terminal), and side panels (roster, tickets, docs,
settings, and related views) wired to the same application protocol the Ink client uses.

## Commands

```sh
npm install          # from webui/
npm run dev          # Vite dev server; proxies /api/ws → live service (see env below)
npm run build        # tsc --noEmit + vite build → webui/dist (index.html + hashed assets)
npm run preview      # serve the production build locally
npm run test         # vitest (ApplicationWebSocketClient + cssVars + component tests)
npm run typecheck    # tsc --noEmit across webui + the aliased @core tree
```

### Dev against a live service

There is **no** unix-socket / bus bridge. The murder service serves browser assets and
`GET /api/ws` itself. `murder web up` ensures the service is running and prints the **browser base
URL** (e.g. `http://127.0.0.1:NNNN`). The WebSocket is `{base}/api/ws` (also published as
`websocket_url` on the service session registry).

For `npm run dev`, point Vite's `/api/ws` proxy at that service:

```sh
# From the repo root — print the browser base URL, then set the WS URL for the proxy:
BASE="$(murder web up)"   # e.g. http://127.0.0.1:NNNN
export VITE_APPLICATION_WS_URL="${BASE/http/ws}/api/ws"
# or explicitly: export VITE_APPLICATION_WS_PROXY=ws://127.0.0.1:NNNN

cd webui && npm run dev
```

Env resolution (first match wins):

| Variable | Meaning |
| --- | --- |
| `VITE_APPLICATION_WS_PROXY` | Proxy origin only, e.g. `ws://127.0.0.1:NNNN` |
| `VITE_APPLICATION_WS_URL` | Full WS URL, e.g. `ws://127.0.0.1:NNNN/api/ws` (host/port extracted) |
| `VITE_BUS_PROXY_PORT` | Deprecated alias: `ws://localhost:$PORT` |

If none are set, the `/api/ws` proxy is omitted. `npm run build` emits **`webui/dist`**, which the
Python service ships as `murder/_webui/` and serves; in that context `/api/ws` is same-origin so no
proxy is involved.

## Reuse strategy — the `@core` alias

`vite.config.ts` and `tsconfig.json` both alias **`@core/*` → `../inktui/src/*`**. The web app
imports the portable core straight off the inktui tree — there is no copy, no fork:

| Imported from `@core` (aliased, reused as-is) | Why it is portable |
| --- | --- |
| `@core/store/store` (`createAppStore`) + every slice | zustand-vanilla only; no ink, no node |
| `@core/hooks/useAppStore` (provider + hook) | react + `zustand/traditional` only |
| `@core/generated/applicationProtocol`, `@core/application/*` | generated public wire + client seam |
| `@core/selectors/*` | pure derived/formatting |
| `@core/theme/buildTheme`, `@core/theme/palettes`, `@core/theme/themeStore` | pure + zustand |

**Reimplemented in `webui/src` (the non-portable parts):**

- `src/application/ApplicationWebSocketClient.ts` — browser `WebSocket` transport for the closed
  application protocol. Owns request correlation, projection subscriptions, terminal attach/detach,
  reconnect/backoff, and status hooks. Mirrors `inktui`'s client; talks to the service's `/api/ws`.
- `src/theme/cssVars.ts` + `src/theme/useThemeCssVars.ts` — project the semantic `Theme` onto CSS
  custom properties (the Ink UI paints `<Text color=…>`; the web UI paints via CSS vars).
- `src/App.tsx`, `src/main.tsx` — DOM renderer + entrypoint (mirror of inktui's `index.tsx`).

A core module that transitively imports `ink` or `node:*` is **not** aliased; none of the modules
the web app uses do (verified: `store.ts`'s transitive closure is zustand + protocol + slices only;
`useAppStore.ts` is react + zustand only).

### React dedupe

Both `package.json` files pin `react`/`react-dom` to `^19.2` (same major, single instance
requirement). Because `@core` resolves out-of-root, Vite could otherwise pull a second React copy
and break hooks; `vite.config.ts` sets `resolve.dedupe: ['react','react-dom']` so the single copy in
`webui/node_modules` is always used. `server.fs.allow: ['..']` lets Vite read+transpile the sibling
inktui TS sources, and `tsconfig` uses `moduleResolution: bundler` so the core's `.js` import
specifiers resolve back onto the `.ts` sources.

## ApplicationWebSocketClient — the service contract

The service owns the typed WebSocket endpoint; the browser speaks the application protocol directly
(no relay framing).

- **Endpoint:** `GET /api/ws` on the serving origin. Default URL in the browser:
  `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`. Override via the
  client `url` option (dev uses the Vite proxy, so same-origin `/api/ws` still works).
- **Outbound / inbound:** each protocol envelope is **one WS text frame** (`JSON.stringify` /
  `JSON.parse`). No line buffering — WebSocket is message-framed.

The first frame is `client.hello` with `APPLICATION_PROTOCOL_VERSION` and a stable `client_id`
persisted in `localStorage`. Queries and commands use correlated `request`/`reply` messages;
projection subscriptions keep independent cursors across reconnects; terminal output uses
`terminal.attach`/`terminal.frame`/`terminal.detach` with a real session UUID (`sessionId === null`
skips attach). Reconnect uses capped exponential backoff with full jitter; a version mismatch is
permanent.

## Styling — CSS custom properties only

**All thematic styling lives in plain CSS files** under `src/styles/`, driven by `--color-*` custom
properties. There is **no CSS-in-JS and no inline thematic style objects**. `useThemeCssVars()`
subscribes to the theme store and writes the active theme's roles onto `:root` on change
(`src/theme/cssVars.ts` does the Theme→vars mapping; `src/styles/theme.css` documents the full
variable contract and carries the default-theme fallbacks).

To restyle: edit the CSS files. To re-theme: switch the theme-store scheme (`setTheme(id)` or commit
through the settings slice) — the variables repaint everything. Never hard-code a hex in a
component; always reference `var(--color-…)`.
