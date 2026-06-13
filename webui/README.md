# webui — web/mobile frontend for murder

A Vite + React 19 + TypeScript app that ports the Ink TUI to the browser. It **reuses the
framework-agnostic core** of `inktui/` (store, selectors, theme, wire protocol) verbatim and only
reimplements the parts that are terminal-specific: the transport (WebSocket instead of a Unix
socket) and the renderer (DOM instead of Ink).

This is the **Wave-1 foundation**: it proves the data spine end-to-end with a minimal UI (header +
live roster + live tickets). The full UI port is a later wave.

## Commands

```sh
npm install          # from webui/
npm run dev          # Vite dev server; proxies /bus → ws://localhost:8473 (a running bridge)
npm run build        # tsc --noEmit + vite build → webui/dist (index.html + hashed assets)
npm run preview      # serve the production build locally
npm run test         # vitest (WsBusClient + cssVars)
npm run typecheck    # tsc --noEmit across webui + the aliased @core tree
```

`npm run dev` expects the bus bridge running locally: `murder web up -f` on **port 8473** (the dev
proxy target — override with `VITE_BUS_PROXY_PORT`). `npm run build` emits **`webui/dist`**, which
the Python bridge ships as `murder/_webui/` and serves; in that served context `/bus` is
same-origin so no proxy is involved.

## Reuse strategy — the `@core` alias

`vite.config.ts` and `tsconfig.json` both alias **`@core/*` → `../inktui/src/*`**. The web app
imports the portable core straight off the inktui tree — there is no copy, no fork:

| Imported from `@core` (aliased, reused as-is) | Why it is portable |
| --- | --- |
| `@core/store/store` (`createAppStore`) + every slice | zustand-vanilla only; no ink, no node |
| `@core/hooks/useAppStore` (provider + hook) | react + `zustand/traditional` only |
| `@core/bus/protocol`, `@core/bus/BusClient` | types + constants only |
| `@core/bus/matchesFilter`, `@core/bus/readEnvelope` | pure helpers |
| `@core/selectors/*` | pure derived/formatting |
| `@core/theme/buildTheme`, `@core/theme/palettes`, `@core/theme/themeStore` | pure + zustand |

**Reimplemented in `webui/src` (the non-portable parts):**

- `src/bus/WsBusClient.ts` — the `BusClient` over a browser `WebSocket`. The UDS client
  (`@core/bus/UdsBusClient`) depends on `node:net`/`node:crypto` and JSON-lines byte framing, none
  of which exist in the browser, so it is reimplemented (not aliased). It speaks the **identical
  protocol** (`@core/bus/protocol`) and mirrors the UDS client's handshake, RPC correlation,
  subscription replay, reconnect/backoff and status hooks — only the framing differs (see below).
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

## WsBusClient — the bridge contract

The Python bridge is a **dumb 1:1 relay**; the browser implements the full protocol.

- **Endpoint:** `GET /bus` on the serving origin. Default URL:
  `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/bus`. Override via the
  `url` option (dev uses the Vite proxy, so same-origin `/bus` still works).
- **Outbound:** each protocol envelope is **one WS text frame**, `JSON.stringify(envelope)` with
  **no trailing newline** — the bridge appends the `\n` when writing to the unix socket.
- **Inbound:** each WS text frame is **exactly one complete JSON envelope**; `JSON.parse` directly.
  No line buffering on the browser side (WebSocket is message-framed, unlike the raw socket).

Everything else mirrors `UdsBusClient`: Hello-first handshake (`client_kind: 'web'`,
`PROTOCOL_VERSION = 5`, a stable `client_id` persisted in `localStorage`), `correlation_id` RPC
pairing with a `timeout_s + 1` deadline, a subscription registry replayed after every reconnect,
exponential backoff with full jitter, and the `onConnect`/`onDisconnect`/`onPermanentError` status
hooks the header reads.

## Styling — CSS custom properties only

**All thematic styling lives in plain CSS files** under `src/styles/`, driven by `--color-*` custom
properties. There is **no CSS-in-JS and no inline thematic style objects**. `useThemeCssVars()`
subscribes to the theme store and writes the active theme's roles onto `:root` on change
(`src/theme/cssVars.ts` does the Theme→vars mapping; `src/styles/theme.css` documents the full
variable contract and carries the default-theme fallbacks).

To restyle: edit the CSS files. To re-theme: switch the theme-store scheme (`setTheme(id)` or commit
through the settings slice) — the variables repaint everything. Never hard-code a hex in a
component; always reference `var(--color-…)`.
