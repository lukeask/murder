# webui — web/mobile frontend for murder

A Vite + React 19 + TypeScript app that shares its renderer-neutral application surface with the
Ink UI through the root `@murder/ui-core` workspace package. The browser app owns DOM components,
CSS, browser defaults, and browser-specific lifecycle only.

The UI is a **cockpit**: header, stage (chat + terminal), and side panels (roster, workflows, docs,
settings, and related views) wired to the same application protocol the Ink client uses.

## Commands

```sh
npm ci                              # once, from the repository root
npm run dev -w webui                # Vite dev server; proxies /api/ws → live service
npm run build -w webui              # typecheck + Vite build → webui/dist
npm run preview -w webui            # serve the production build locally
npm run test -w webui               # browser component and factory tests
npm run typecheck -w webui
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

npm run dev -w webui
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

## Shared UI boundary

`ui-core/` owns application interfaces and transport, generated protocol types, Zustand state,
selectors, renderer-neutral React hooks, themes, input-domain helpers, and workflow logic. Import
them by explicit subpath, for example
`@murder/ui-core/store/store.js`; the package deliberately has no giant root barrel.

`webui/src/` must retain DOM components, CSS variables and styles, browser URL/client-ID defaults,
focus and scroll behavior, and browser composition. `inktui/` retains Ink components, terminal
rendering and input, CLI/process behavior, and its TUI client defaults. Neither application imports
source from the other.

Vite aliases `@murder/ui-core` to `ui-core/src` for fast workspace development and bundles it with
the web app. The workspace lockfile hoists one pinned React runtime shared by both frontends.

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
