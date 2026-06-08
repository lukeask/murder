# inktui — the Ink terminal UI

The Ink (React-for-terminal, TypeScript) rewrite of the murder TUI. Replaces the Textual
app under `murder/app/tui/`. Talks to the service over the **existing Unix-socket JSON-RPC
bus** (`murder/bus/`).

**Plan + decision record:** `.murder/plans/newui-inktui.md`. Read it before writing code.
The agent work plan (carved chunks) lives at the bottom of that file.

> The scaffold (chunk **C0**) has landed: toolchain, strict TS, the green-gate scripts, and the
> directory skeleton. Later chunks fill it in one agent at a time. Do not scaffold ad hoc —
> follow the chunks so the production patterns land before feature work. See § Toolchain and §
> Layout below; each `src/*` directory has its own one-line README of what belongs there.

## The five rules (the layer cake — do not violate)

These exist because the Textual app rotted into a 2200-line god-object. Every chunk reinforces
them so the *path of least resistance* is the correct pattern, not the old one.

1. **Components are pure functions of a slice.** Local UI state (cursor/scroll/expanded) via
   `useState`. Zero bus knowledge. `React.memo` + a narrow selector is the *standard*, not an
   optimization pass.
2. **Presentation lives in selectors, not the store.** Sorting, truncate-to-width,
   parent-indent, column tuples — all `useMemo` view-models. The store stays reusable by a
   future React-DOM (web/phone) client.
3. **Actions are the only view→bus path.** Components never touch the bus. The store's action
   layer is the sole caller of `BusClient`.
4. **The store is framework- & transport-agnostic.** No Ink/terminal/socket assumptions in any
   slice or action. `BusClient` is an **injected interface** so tests fake it and a future
   WebSocket bridge swaps transport with zero store edits.
5. **Input/focus is data, not gating.** One root `useInput` dispatcher; panels *declare* their
   keymaps; focus is a state machine with a *derived* candidate set. No `check_action`-style
   central gating table, no scattered imperative re-homing.

## Anti-patterns from the old TUI — do not port

- The poll-everything-every-tick `IngestionCoordinator`. The new store is **event-driven**:
  `state.snapshot` key-only events invalidate exactly the named slice. No deep-equality diff
  engine is ported — the wire carries the change granularity.
- Stringly-typed `_view` / `_active_document` and conversation-id string-prefix parsing. Use
  **discriminated unions** for agent identity and a `panelStore` toggle set for view state.
- The hand-rolled `useSyncExternalStore` mixin (`StoreComponent`) and the ~2200-line
  `MurderApp`. Zustand + `useStore(selector, shallow)` is the store layer; the app is a
  decomposed component tree.

## Toolchain

The stack, and why each piece is what it is. These are the choices every later chunk inherits;
change them deliberately, not casually.

| Concern | Choice | Why |
|---------|--------|-----|
| Language | **TypeScript 6** (strict) | Typed seam (`BusClient`, wire protocol, discriminated-union agent identity) is the whole point; see § tsconfig flags. |
| View | **Ink 7** (React 19) | React-for-terminal — our instincts are React (see plan § Why Ink). Thin view over a store we own. |
| Store | **Zustand 5** | *Is* the `useSyncExternalStore`+actions shape, TS-first; `useStore(selector, shallow)` gives referential stability per selector for free. |
| Tests | **Vitest 4** + **ink-testing-library 4** | Vitest shares esbuild/tsx so no separate test-build; ink-testing-library asserts on the painted frame (component-test idiom). |
| Dev runner | **tsx** | Runs `.tsx` directly for `npm run dev`; no watch-build needed for the smoke loop. |
| Lint + format | **Biome 2** | One tool, one config, one binary for both lint and format — see below. |

### Lint/format decision: Biome (not ESLint + Prettier)

Biome was chosen over ESLint+Prettier deliberately:

- **One tool, one config, one dependency.** ESLint+Prettier is two tools, two configs, a
  plugin matrix (`@typescript-eslint`, `eslint-config-prettier`, `eslint-plugin-react-hooks`)
  and the perennial lint-vs-format conflict. Biome is a single binary that lints *and* formats
  with no integration glue — fewer moving parts for the weaker agents who follow to misconfigure.
- **Speed.** Biome is ~10–100× faster, so the green-gate stays cheap to run on every chunk.
- **Strictness we need is built in.** `useExhaustiveDependencies` (the React-hooks deps rule),
  `noExplicitAny`, and `noUnusedImports` are promoted to errors in `biome.json`, covering the
  footguns this codebase cares about without a plugin zoo.

The tradeoff: Biome's rule set is narrower than the full ESLint ecosystem. That is acceptable
here — the type checker carries most of the load, and a smaller rule surface is easier to keep
consistent across many agents. If a future chunk needs a rule Biome lacks, add it to
`biome.json` or revisit this decision in the plan, do not bolt ESLint on beside it.

### Why these tsconfig flags

Full strictness is deliberate. The non-default flags, grouped:

- `strict` + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` — the core soundness
  set. Array/record access yields `T | undefined` (the bus delivers partial slices; the
  compiler forces the undefined case), and optional props can't silently become `undefined`.
- `noImplicitOverride`, `noFallthroughCasesInSwitch`, `noImplicitReturns` — the
  discriminated-union dispatch (agent identity, event keys) is switch-heavy; these make an
  un-handled case a compile error, not a silent fall-through.
- `noUnusedLocals` / `noUnusedParameters` / `noUnusedImports` (Biome) — dead code never
  accumulates, the rot that sank the old app.
- `noPropertyAccessFromIndexSignature` — index-signature reads must use `obj['key']`, so a
  typo on a *typed* field is caught.
- `verbatimModuleSyntax` + `isolatedModules` — required for the tsx/esbuild single-file
  transpile path; forces `import type` for type-only imports (keeps runtime imports honest).
- `NodeNext` module resolution — Ink 7 is ESM-only; relative imports carry the `.js`
  extension (TS resolves them to `.ts`/`.tsx`). This is why source uses `./components/App.js`.

## Layout

```
inktui/
  src/
    bus/         transport seam: BusClient interface, FakeBusClient, UdsBusClient, protocol.ts
    store/       Zustand slices + actions (the only view->bus path)
    selectors/   useMemo view-models (presentation lives here, not the store)
    components/  Ink components (pure functions of a slice, React.memo + narrow selector)
    input/       focusStore, panelStore, keymap-as-data, root useInput dispatcher
    hooks/       reusable hooks binding components to stores/selectors
    index.tsx    process entrypoint (renders the Ink tree)
  test/          Vitest suites (mirror src paths)
  package.json   scripts: dev / build / typecheck / lint / test
  tsconfig.json       build config (src -> dist, strict, composite)
  tsconfig.test.json  typecheck-only config covering test/ (no emit)
  biome.json          lint + format
  vitest.config.ts    test runner
```

Each `src/*` directory carries a one-line README stating what belongs there — read it before
adding a file, so code lands in the layer the cake assigns it.

## Scripts (the green gate)

`build`, `typecheck`, `lint`, `test` must all pass before a chunk is done — this is the gate
every chunk after C0 inherits.

| Script | Command | Does |
|--------|---------|------|
| `npm run dev` | `tsx src/index.tsx` | Renders the Ink app (C0: prints a banner and exits clean). |
| `npm run build` | `tsc --build` | Emits `dist/` (JS + `.d.ts` + sourcemaps). |
| `npm run typecheck` | `tsc --build && tsc --noEmit -p tsconfig.test.json` | Typechecks src (build) and tests (no emit). |
| `npm run lint` | `biome check src test` | Lints + format-checks. `npm run lint:fix` writes fixes. |
| `npm run test` | `vitest run` | Runs the Vitest suite once. |
