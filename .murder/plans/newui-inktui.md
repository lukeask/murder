---
created_at: '2026-06-07T00:00:00'
name: newui-inktui
parent: newui
related_plans: [newui, newui-service, plan-tui-data-render-split, plan-tui-component-library]
status: draft
phase: null
---

# New UI — Ink TUI (frontend)

The **frontend** half of the `newui` rewrite: the terminal UI rebuilt in **Ink** (React for
the terminal), replacing Textual. See [[newui]] for the full concept + the Textual→Ink
architecture decision, and [[newui-service]] for the backend it consumes.

**Scope now: the TUI only.** Ink replaces Textual for the terminal UI, talking to the
service over the **existing Unix-socket JSON-RPC bus**. A WebSocket bridge and the web /
phone frontends are **deferred** (see "Deferred" below) — but we design for them now by
keeping the store framework- and transport-agnostic, so adding them later is wrapping, not
rework.

This plan implements the newui **interaction spec** as an Ink client over the shared headless
store. Design every panel as a store-subscribing component with no backend coupling — the
store + action layer is the artifact a later web/phone client reuses (its view layer would
be React-DOM, not Ink; what's shared is the store/logic/hooks, not the terminal widgets).

**Depends on [[newui-service]] landing** — specifically the bus-surface cleanup (B13 / the
V-list) so this client is a pure RPC consumer. Tickets here are sketched; carve them
concretely once the backend is stable.

Status: draft — held until backend (`newui-service`) is in flight.

## Goal

One **composable view** where `ctrl+<n>` toggles independent panels, a unified focus ring,
crows/collaborator/planners/rogues in one place, generalized starring + doc-toggling — all
in Ink, fed by the store over the bus. "Done" = the Textual app is retired and the Ink TUI
reaches feature parity with the new spec, with the store + action layer left reusable by a
later web/phone client (not built here).

## Guiding principles

- **Numbers map to screen position.** `1`(leftmost)..`4` left panels; `9`,`0`(rightmost)
  right panels. `5`–`8` reserved (history, settings, …).
- **Store is the shared artifact.** Components subscribe to the `useSyncExternalStore`-shaped
  store; no direct bus calls scattered in views — go through store actions. The same store
  will back a later web/phone client; that's a design constraint, not work in this plan.
- **Pure RPC consumer.** No filesystem/DB access; everything via the store → bus. (Backend
  closes the remaining couplings in [[newui-service]] B13.)
- **Vim-in-place editing.** Use an Ink vim-emulator for the ticket/plan/note editors so
  editing keeps the surrounding panels visible — no `$EDITOR`-suspend blanking.
- **Zustand is the store layer.** It *is* the existing `useSyncExternalStore`+actions shape,
  TS-first; `useStore(selector, shallow)` gives referential-stability-by-selector for free
  (the over-render guard, not hand-rolled). See Architecture below.

## Architecture (locked 2026-06-08)

Settled in the 2026-06-08 design discussion. The store/action layer is the reusable artifact;
the rest is the decomposition Textual blocked, done right in Ink.

### Meta-diagnosis

The headless store was always the right backbone; the amateurish part was the ~2200-line
`MurderApp` god-object that Textual prevented from getting a real React decomposition —
stringly-typed `_view` / `_active_document`, conversation-id string-parsing for chat routing,
three hard-coded per-view focus candidate lists, scattered imperative focus re-homing, a
hand-rolled `useSyncExternalStore` mixin (`StoreComponent`), and presentation baked into store
`rows`. The Ink port is **finishing that decomposition**, not translating it.

### Layer cake (top → bottom)

| Layer | Responsibility | Rule |
|-------|----------------|------|
| **Components (Ink)** | pure functions of a slice; local UI state (cursor/scroll/expanded) via `useState` | zero bus knowledge; `React.memo` + narrow selectors as the standard, not an optimization pass |
| **Selectors / view-models** | `useMemo`: sort, truncate-to-width, parent-indent, column-tuple | **presentation lives here, not the store** — keeps the store reusable by a future DOM client |
| **Stores + actions (Zustand)** | domain slices fed by bus events; actions are the **only** view→bus path | framework- & transport-agnostic; no Ink/terminal/socket assumptions |
| **Focus / panel input stores** | `focusStore` (state machine), `panelStore` (`Set<PanelId>`), keymap-as-data | one root `useInput` dispatcher; no `check_action`-style central gating |
| **BusClient** | single Unix-socket JSON-RPC client; reconnect/backoff/error policy | **dependency-injected** so tests fake it and the future WS bridge swaps transport with zero store edits |

### Data flow — event-driven slice invalidation

No poll-everything-every-tick. The service emits change-granular events (the existing
`state.snapshot` key-only bus events); the Node store **ref-swaps only the changed slice**, so
only that slice's subscribers re-render. The Python deep-equality diff engine is **not** ported
— the wire protocol carries the change granularity instead. This is the perf story that
replaced Textual.

### Input & focus (patterns that kill the old smells)

- **Focus as a state machine.** `focusStore` holds `focusedId`; candidate set is *derived*
  (`[...visiblePanels, chatInput]`); the re-home invariant ("focused hidden → chat") is one
  derived effect, not scattered re-homing. The geometric `_directional_focus_target` kernel
  ports as a pure fn over measured rects (Ink `measureElement`) for `ctrl+vim` nav.
- **Layered input dispatch.** One root `useInput`: chat-focused short-circuits to the input →
  global chords (`ctrl+<n>`, `ctrl+vim`, `ctrl+y`, `ctrl+s`) → else delegate to the focused
  panel's declared keymap. Panels **declare** their keys (keymap-as-data); no central gating
  table.
- **Discriminated unions over stringly-typed state.** Agent identity is a tagged union
  (`{kind:'collaborator'} | {kind:'planner',plan} | {kind:'rogue',id} | {kind:'ticket',id}`),
  not conversation-id string-prefix parsing. View state is the `panelStore` toggle set, not a
  `_view` enum.

### Raw-tmux (`ctrl+y`)

tmux frames stream over the bus as a subscription **opened only in raw mode** → no standing
perf overhead, nothing kept live when parsed view is showing. Residual is rendering one ANSI
frame in Ink while active (contained, small). This resolves the prior terminal-emulation watch
item.

## Bus contract (shared — keep [[newui-service]] and [[newui-inktui]] in sync)

> **This block is duplicated verbatim in both plans.** It is the one interface the two halves
> build against, so service and Ink can be developed **in parallel** without reading each
> other's internals. Change it in both copies, or not at all. The service **implements** this
> surface; the Ink store **consumes** it.

**Transport.** One Unix-socket JSON-RPC connection, multiplexed. The service is the sole
authority over DB + filesystem; the view never touches disk/DB — every read and write is a
method call or a subscription on this socket. (A later WS bridge re-exposes this *same*
surface; designing to it now is what makes the bridge cheap — see "Deferred" below.)

**Direction & layering.**
- view → service = **RPC methods** (request/response). The store's **action layer is the only
  caller**; components never touch the bus.
- service → view = **events** (server-push). Events are **key-only** — they name the slice
  that changed; the view re-pulls that slice. No full-payload pushes, no poll-everything tick,
  no ported Python deep-diff; the wire carries the change granularity. The store ref-swaps the
  named slice and only its subscribers re-render.

### Methods (view → service)

*Already on the bus (consumed as-is):*
- `ticket.quick_kick {…}` — kick an existing ticket.
- `crow.spawn_rogue {…, effort}` — spawn a rogue/crow; payload **carries `effort`** end-to-end (service B10).
- `agent.message {agent_id, message}` — deliver a message to an agent.
- `notetaker.capture.submit {…}` — submit a captured note.

*New — added by service B13/C14 (the V-list closure; F0 depends on these existing):*
- `ticket.quick_create {title}` → `{handled, ticket_id, title}` (replaces the direct `.md` write, V1).
- `ticket.next_id {}` → next free ticket id (V4).
- `ticket.exists {handle}` → bool (V5).
- `editor.binary {preferred?}` → `{ok, editor}` — **CHOSEN over `editor.open`**: the service is a
  tty-less daemon, so it resolves and returns the editor *command*; the client launches the
  subprocess locally (V6).
- `image.upload {bytes (base64), ext?}` → `{ok, path}` — **CHOSEN over extending
  `notetaker.capture.submit`**: images are pasted inline mid-draft and referenced as
  `![image](path)` before submit, and several may exist per note (V2).
- `tui.save_favorites {favorites: [id,…]}` → `{ok, favorites}` — persist favorite ids; **paired
  with `tui.load_favorites {}` → `{ok, favorites: [id,…]}`** (both directions had to leave
  `.murder/`) (V3).
- `worktree.list {}` → `{ok, entries: [{path, branch, is_main},…]}` — list `.murder` worktrees
  (V7: demotes the direct `list_murder_worktrees_sync` import).

### Events / subscriptions (service → view)

- `state.snapshot` — **key-only** change events; the store invalidates exactly the named slice.
- **tmux frame stream** — a subscription opened **only in raw mode** (`ctrl+y`): streams ANSI
  frames for the focused pane, closed when parsed view returns (no standing cost).

### Payload / DTO shapes crossing the boundary (changing in this rewrite)

- **`ScheduleTicketRow.pending_dep_ids: tuple[str,…]`** replaces `deps_ok: bool` — the row
  carries the ids of *non-done* deps; the Ink deps cell renders them (service B5).
- **Ticket** = frontmatter (`title, deps, harness, model, worktree`) + body. **Runtime state
  (`status, schedule_at, attempts`) is DB-only** — delivered in the row DTO, never in the doc
  the editor shows.
- **Checklist** rides in the ticket body under `# Checklist` (`[ ]`/`[x]`); the editor toggles
  with a normal body edit; the service syncs body→DB.
- **`parent`** on plan rows drives parent/child indentation (service supplies; Ink indents).
- **`effort`** is a per-harness enum the Ink spawn wizard collects and passes through
  `crow.spawn_rogue` (above).

### Invariants both sides rely on

- Service is **renderer-agnostic**: no Ink/terminal/web assumption in any method or event;
  presentation (sort/truncate/columns/indent) is the view's job, never the row DTO's.
- View is a **pure RPC consumer**: no `.murder/` file or DB access; the B13/V-list closure is
  the precondition (F0 depends on service B13).
- **Key-only events** mean the service can add new change-keys without breaking the view, and
  the view ignores keys it doesn't subscribe to — the two evolve independently between contract
  revisions.

## Why Ink (decision rationale)

Captured from the 2026-06-07 discussion (see [[newui]] for the short version).

- **Our instincts are React, and Textual is React-flavored but not React.** A reviewer's
  full list of "you're using Textual wrong" reduced to one thing with many faces: we kept
  writing it the React way (reimplemented `useSyncExternalStore`, hand-rolled conditional
  rendering and effect cleanup, eager constructors). When every divergence is "you keep
  doing React," the framework isn't ours.
- **The headless store is not a mistake to undo — it's the correct backbone.** A
  framework-agnostic logic core with thin per-renderer views is the right shape. It was
  *wrong-for-Textual* (Textual wants to own state) and is *right-for-Ink* (Ink is a thin
  view over a store you own — the idiomatic React shape). Doing "Textual right" would mean
  coupling state into reactive attributes / `watch_` / `data_bind`, which destroys the
  portable backbone. Ink keeps it.
- **Performance.** Textual perf became a real problem; leaving Python for the frontend
  addresses it on its own. (Rust was considered; sharing language/components with the
  eventual React web/phone clients tipped it to Ink.)
- **The widget counterweight, and why it's small for us.** Ink is lean on batteries and our
  UI is widget-heavy (lists, doc/markdown viewers, chat pane, ticket grid, dispatch/mirror
  panes). Textual's `DataTable` / `Markdown` / `ListView` / `Tree` / `TextArea` are genuine
  labor to replace. Two things make the cost acceptable: (1) we're custom-designing these
  panels from scratch *either way* — we don't lean on Textual's stock widgets — so the
  rebuild isn't extra; (2) a vim-emulator package covers in-TUI editing, replacing the most
  expensive widget (a full editor) and the `$EDITOR`-suspend hack. The `ctrl+y`
  parsed-vs-raw tmux pane was the one feared widget (terminal-in-terminal emulation); it is
  **resolved by streaming tmux frames over the bus, subscribed only in raw mode** — see
  Architecture › Raw-tmux below. The residual is rendering one ANSI frame in Ink while
  active, a small contained concern, not a standing perf cost.
- **Timing.** The TUI is getting rewritten regardless, so this is the only sane moment to
  switch frameworks — otherwise we'd build newui twice. Sunk cost is nonzero (the render
  half of phases 1+2 is scrapped) but the store, bus, and backend survive, and a clean slate
  sheds the old vibecoded Textual patterns.

## Approach (interaction spec, locked 2026-06-07)

### Layout

- One view. Always visible: top bar, bottom bar, chat input.
- **Left panel** (visible if any of 1–4 active): `1` plans · `2` notes · `3` reports ·
  `4` tickets. Wider than today's `ctrl+b`. Two-lineheight entries for all four.
- **Right panel** (visible if 0 or 9 active): `9` usage · `0` crows. Usage sits to the left
  of crows.
- Scheduler moved to settings. Calendar panel cut from the UI (returns after the scheduler
  overhaul).

### Focus manager (rewrite — do it right in Ink)

- Single **dynamic** candidate set = currently-visible panels + chat input.
- Invariant: after any toggle, if the focused widget is now hidden → re-home to chat input.
  (Kills the "nothing highlighted, must ctrl+f" bug class.)
- Always exactly one border highlighted. `ctrl+vim` navigates the visible set.
- `ctrl+<n>` brings highlight to that component, toggling it on if currently off.
- `ctrl+f` → chat. `ctrl+s` → highlight to text input, which becomes the spawn wizard.

### Left panels (1–4)

- **Tickets (4):** 2-row-per-ticket layout; column groups `id/title · status/last-update ·
  deps/schedule · harness/model · plan/worktree`; alternating color every 2 lines. Deps cell
  renders the `pending_dep_ids` from the backend (only non-done dep ids). Ticket editor
  (enter on highlighted ticket) shows the body; checklist editable `[ ]`→`[x]` via the
  vim editor. Remove the empty "no model override" option (backend already requires explicit
  harness+model). Free-form schedule input (`1d4h3m`, `34m`, …) using the backend
  `parse_duration()` util — no radio presets.
- **Plans (1) / Notes (2) / Reports (3):** list with two-lineheight entries.

### Starring & document toggling (generalized)

- `ctrl+s` stars the highlighted plan/note (and crow). Starred shown at top. Generalize the
  crow-favorites prefs pattern to plans/notes (via the backend prefs RPC, not direct file).
- `enter` on a highlighted plan/note/report toggles showing the doc in the TUI; `enter` on a
  shown doc minimizes it and returns highlight to its list (enter again restores).

### Parent plans (display)

- Children listed under parent, name indented 4 spaces. A child's more-recent update counts
  for the parent's ordering position. (Backend supplies the `parent` field.)

### Crows panel (0) + usage (9)

- Crows organized **by type:** collaborator → planning agents → rogue crows → ticket crows.
  (Backend re-includes collaborator + planners.)
- Always-on minimized right view + toggle to maximized extended view.
- collaborator favorited by default; rogue crows favorited on creation;
  favorited = history panel shown for it.
- `ctrl+s` while chatting a crow stars it and keeps that chat pane active.
- (Exploratory) chat-tile layout: one big + many small, `ctrl+h/l` promotes one to big.
- Usage (9): the extended usage component, right-aligned, to the left of crows.

### Keybinds

- `ctrl+p`: new plan popup — box to message a fresh planning agent + plan name.
- `ctrl+t`: **new** — new ticket popup (analogous to `:ticket`); base template the user fills.
- `ctrl+y`: toggle tmux vs parsed view TUI-wide, any view.
- `ctrl+1234567890`: bring highlight/TUI state to that component (toggle on if off).
- `ctrl+s` spawn-context (**new**): when a note/plan/report is the **focused** doc
  (*focused-doc-wins* — list row or opened doc widget alike), the spawn wizard adds a step
  "include `{title}` as context? [yes]/no", default yes.
  - **Mechanism: reference-by-path** (locked). The kickoff message tells the rogue to *read*
    `.murder/<dir>/<name>.md` rather than inlining the body — the read tool-use lands the doc
    as something it actively did, priming engagement (same rationale as ticket crows reading
    their own ticket: a checklist read as "the user wants this" binds the agent to it).
- Spawn wizard collects **effort** (per-harness options) and passes it through the
  `crow.spawn_rogue` RPC (backend B10 carries it end-to-end).

### Top / bottom bars

- Top bar: highlight currently-*toggled* panels (not just the active view). Subscript number
  labels: `plans_1` … `usage_9`, `crows_0`.
- Bottom bar: contextual hints.

## Deferred: WebSocket bridge + web + phone

Real and wanted ("be murderin' from the couch on my phone"), but **out of scope for this
plan** — the Ink TUI ships first, over the existing Unix-socket bus. Deferred:

- **WebSocket bridge** — one Python process wrapping the *same* RPC surface for browser
  clients (a browser can't speak a Unix socket). Pure transport adapter; no new domain logic.
- **Web frontend** — React-DOM, reusing the store + action layer + hooks from the Ink work;
  its own view components (DOM, not terminal).
- **Phone frontend** — same store/logic, mobile React view layer.

**Why it's safe to defer:** nothing here blocks the TUI, and we keep the additions cheap by
designing for them now —
- the store + action layer stay **framework- and transport-agnostic** (no Ink/terminal
  assumptions, no socket assumptions baked into store actions);
- **all** data flows through the bus RPC surface (the service plan's B13 closes the last
  direct-coupling holes), so the WS bridge later just re-exposes that surface;
- what's reused across frontends is the **store/logic/hooks**, not the rendered widgets —
  so committing to Ink's terminal renderer now costs the future web/phone clients nothing.

So: build the TUI on the socket bus; when web/phone come, add the WS bridge and a DOM view
over the same store. Wrapping, not rework.

## Work breakdown (sketch — carve concretely once backend lands)

| ID | What | Deps |
|----|------|------|
| F0 | Ink app scaffold + **injected Unix-socket JSON-RPC `BusClient`** (reconnect/backoff; WS bridge deferred); **port the headless store to Zustand**; event-driven slice invalidation off `state.snapshot` events (no full-poll, no ported Python diff); store-action layer as the only view→bus path | [[newui-service]] B13 |
| F1 | Composable-view shell: panel-toggle framework (`ctrl+<n>`), the **focus-manager rewrite** (dynamic candidate set + re-home invariant), top/bottom bars | F0 |
| F2 | Left panels — plans/notes/reports lists + tickets (2-row layout, deps cell, ticket editor w/ vim, free-form schedule input) | F1 |
| F3 | Right panels — crows-by-type (minimized + maximized, favorites, chat panes) + usage | F1 |
| F4 | Starring + document toggling (generalized, via prefs RPC); parent-plan indentation | F2 |
| F5 | Dialogs — `ctrl+p` new-plan, `ctrl+t` new-ticket, spawn wizard (effort + spawn-context/reference-by-path), `ctrl+y` tmux/parsed toggle | F1 |
| F6 | Retire the Textual app; parity pass; confirm store/components are reusable by web/phone | F2–F5 |

Vim-emulator package selection for in-place editing is an F2/F5 spike. Crow chat-tile
big/small layout (F3) is exploratory and may slip to a follow-up.

## Agent work plan — carved chunks (2026-06-08)

Code lives in **`inktui/`** (repo-root sibling of `murder/`; see `inktui/README.md`). Each
chunk below is sized for **one Claude Code agent, one at a time, sequential** — every chunk
starts from the previous chunk's green tree and leaves the tree green again.

### Why the ordering matters (read this first)

The Textual app rotted because patterns were never established up front, so every addition
copied the last ad-hoc thing. To prevent a repeat: **Opus agents write the backbone (C0–C5)
until every layer of the cake has one fully-worked, tested reference implementation.** Only
then do Sonnet agents take over feature build-out (C6+), each chunk pointing at the exact
reference to copy. The goal of C0–C5 is that the *easy* thing for a later agent to do is the
*correct* thing — copy the plans-panel to make the notes-panel, copy the roster-slice to make
the tickets-slice. If a later agent has to invent a pattern, the backbone wasn't finished.

### Definition of done — applies to *every* chunk

1. `npm run build`, `typecheck`, `lint`, and `test` all pass. The tree is green for the next
   agent. (C0 establishes these scripts; from C1 on they are a hard gate.)
2. The chunk ships its **own tests** (Vitest for store/selector/logic; `ink-testing-library`
   for components). No chunk lands untested patterns.
3. **No stub TODOs inside a committed pattern.** A reference implementation that later agents
   copy must be complete and correct, or it propagates the defect.
4. Update this table's status line for the chunk; note any contract surprises against the
   [Bus contract](#bus-contract-shared--keep-newui-service-and-newui-inktui-in-sync) section.
5. The five rules in `inktui/README.md` hold. A diff that violates a layer-cake rule is not
   done even if it works.

### Backbone first — un-blocked from the backend

C0–C5 build against the **`FakeBusClient`**, so they do **not** wait on `newui-service` B13.
The real socket (C2) and live RPCs land alongside or after B13; the backbone is developed in
parallel. Only feature chunks that need a *new* RPC (e.g. `ticket.quick_create`) hard-depend
on B13 — flagged per row.

---

### Phase A — Backbone & patterns (**Opus**, sequential; each is a pattern anchor)

| ID | Chunk | Pattern it anchors | Deps |
|----|-------|--------------------|------|
| **C0** ✅ **done** | **Scaffold & toolchain.** `inktui/` as a TS/Node project: Ink 7 + React 19 + Zustand 5 + Vitest 4 + `ink-testing-library` 4, **strict** `tsconfig` (TS 6), **Biome 2** for lint+format (rationale in README), `dev`/`build`/`typecheck`/`lint`/`test` npm scripts — all verified green. `npm run dev` renders a "hello" Ink app and exits clean. Directory skeleton (`src/{bus,store,selectors,components,input,hooks}`, `test/`) created, each with a one-line README. | The toolchain + green-gate every later chunk inherits; the directory map that tells agents where code goes. | — |
| **C1** ✅ **done** | **Wire types + BusClient seam.** Port the wire contract to `src/bus/protocol.ts` from `murder/bus/protocol.py` (envelope + the event/RPC shapes in the [Bus contract](#bus-contract-shared--keep-newui-service-and-newui-inktui-in-sync); keep `PROTOCOL_VERSION` in sync). Define the **`BusClient` interface** (typed `rpc(method, params)` request/response + `subscribe(event)` async iterator/callback). Ship a **`FakeBusClient`** test double that scripts events + canned RPC replies. **No real socket yet.** *Landed:* `protocol.ts` (1:1 port, `PROTOCOL_VERSION = 1`; `WireMessage`/`BusEvent` discriminated unions), `BusClient.ts` (typed `rpc` via `RpcMethods` registry; `subscribe(listener, filter?) → Unsubscribe` — **callback delivery**, not async iterator, to match the Zustand/`useSyncExternalStore` observer shape), `FakeBusClient.ts` (`emit`/`stubRpc`/`rpcCalls`/`subscriberCount`); 26 Vitest tests; build/typecheck/lint/test green. | The transport-agnostic seam (rule 4) and the test double the whole backbone is built against. | C0 |
| **C2** ✅ **done** | **Real Unix-socket client.** `src/bus/UdsBusClient.ts` implementing `BusClient`: JSON-lines framing, Hello handshake + `PROTOCOL_VERSION` refusal, reconnect/backoff, and one explicit error policy. Mirrors `murder/bus/client.py` framing. *Landed:* **single persistent multiplexed connection** (deliberate divergence from Python's short-lived-per-RPC + long-lived-per-sub split — justified in the module docstring; observable behavior identical: Hello-first handshake, `correlation_id` pairing, wake-skip on every path). `LineBuffer` reassembles partial/multi-message reads. **Reconnect = exponential backoff + full jitter, capped** (base 250ms / cap 10s; attempt counter resets on a clean handshake; version-mismatch is permanent, never retried; `close()` stops it). **Error policy (one place, the module docstring):** RPC rejects on timeout (`rpcTimeoutS + 1.0`), `err` envelope, or connection drop with the call outstanding; **subscriptions auto-re-establish on every reconnect** (client re-sends each `sub` frame — the store never re-subscribes); connection loss is otherwise invisible to callers. All deps injected (`socketPath`/`clientKind`/`clientId`/`rpcTimeoutS`/`backoff`/`clock`/`logger`) — rule 4. Tests stand up an in-process `net.createServer` Unix socket (no live service): handshake, version-mismatch refusal, rpc round-trip via `correlation_id`, rpc-timeout, partial-frame reassembly, multi-message-per-chunk, wake-skip, unsubscribe, reconnect+re-handshake, subscription re-establishment, outstanding-rpc rejection on drop, close. 39 Vitest tests; build/typecheck/lint/test green. **C1 seam unchanged** (`BusClient.ts` untouched). | Reconnect/backoff/error policy in one place; proves the seam swaps real↔fake with zero store edits. | C1 |
| **C3** ✅ **done** | **Store core + reference data vertical.** Zustand store-creation pattern; the **slice** pattern; **event-driven slice invalidation** (subscribe `state.snapshot` key-only events → re-pull only the named slice → ref-swap so only its subscribers re-render — *not* the old poll-everything `IngestionCoordinator`, *not* a ported deep-diff). Implement **one** slice end-to-end (roster or schedule) with its **action** (the only view→bus caller, rule 3) and a **selector/view-model** (`useMemo` presentation, rule 2). Tests drive it via `FakeBusClient`. *Landed:* chose **roster** (crows). **One root store, slice-per-key** built with `zustand/vanilla` `createStore` (no React import — rule 4); slices are flat top-level keys so a ref-swap of one key is shallow-comparable. Files: `store/store.ts` (composition root + invalidation table), `store/roster/rosterSlice.ts` (presentation-free `RosterState`/`RosterRow` + `StateCreator`), `store/roster/rosterActions.ts` (the **sole** bus caller; DTO→row projection; loading/error into the slice), `selectors/rosterSelectors.ts` (pure `selectRosterView` + `useRosterView` memo — sort/basename/truncate live here, rule 2), `hooks/useAppStore.ts` (React adapter: context + `useStoreWithEqualityFn` so `useAppStore(sel, shallow)` gives per-selector referential stability — rule 1). **Mechanism:** `createAppStore(bus)` subscribes filtered to `state.snapshot`; on an event it maps `event.entity` → the slice whose `*_INVALIDATING_ENTITY` matches → calls that slice's `refresh` action → `setState({ roster })` ref-swaps **only** that key (siblings keep identity → no over-render). Roster invalidates on `entity:'agent'`. **RPC:** `crow.get_snapshot` (params `{}` → `CrowSnapshotReply`) — **NOT yet on the live bus**; modeled per the Bus contract (`domain.verb`), mirrors Python `RuntimeClient.get_crow_snapshot()`. Declared via `declare module '../../bus/BusClient.js'` augmentation of `RpcMethods` **from the slice**, so the C1/C2 bus files stay byte-identical (seam held — confirmed unchanged). To add slice X: copy the three roster files, rename state/row/RPC/projection, point at X's `Entity`, add one `invalidations` entry + one `actions` key in `store.ts`. 16 new Vitest tests (granularity proof: matching event → 1 rpc + roster ref-swap, sibling `actions` identity preserved; unrelated/non-snapshot event → 0 rpc; error→slice.error; loading flag; selector view-model). build/typecheck/lint/test green (55 tests). **Update (post-C7, dup-smell removal):** the four slice/action quads were ~80% byte-identical, so the identical `{ rows, status, error }` shape + loading/error/ref-swap-only-this-key mechanics were extracted into a shared **`store/listSlice.ts` factory** (`ListState<Row>` + `initialListState` + `createListSlice(key, initial)` + `createRefreshAction(bus, store, {key, method, project})`). The slice/action files are now **thin shells** over it — each supplies only its row type, RPC method (+ reply type), and DTO→rows `project` fn. The per-domain divergence (notably tickets' active+recent_done+archived 3-bucket flatten) lives in that injected `project`; the generic never special-cases a domain. So the recipe is now: **copy the three roster files** (still 3 files), but each is a few domain-specific lines over the factory — no `{rows,status,error}` mechanics to re-derive. `store.ts` wiring (the ≈5 additive edits) is unchanged. The factory has its own focused test (`test/store/listSlice.test.ts`); the C3 granularity proofs in `store.test.ts` are unchanged and still pass, now exercising the factory through every domain. | THE reference for every future slice/action/selector. "Copy the roster slice" must be the obvious move. | C1 |
| **C4** ✅ **done** | **Input & focus backbone.** `focusStore` (state machine: `focusedId`, **derived** candidate set = `[...visiblePanels, chatInput]`, re-home invariant as one derived effect); `panelStore` (`Set<PanelId>`); **keymap-as-data**; single root `useInput` dispatcher (chat-focused short-circuit → global chords `ctrl+<n>`/`ctrl+vim`/`ctrl+y`/`ctrl+s` → else focused panel's declared keymap). Port `_directional_focus_target` as a pure fn over measured rects. Wire two dummy panels + chat so toggle/nav/re-home are demonstrably correct and **tested** (simulated key sequences). *Landed:* all input code in `src/input/` (framework-agnostic vanilla Zustand) + React glue in `src/hooks/`. **`panelStore`** (`src/input/panelStore.ts`): `visible: ReadonlySet<PanelId>` with `toggle/show/hide` (ref-swaps a new Set each change; idempotent ops keep identity). `PanelId`/digit→panel mapping is one total table (`src/input/panels.ts`, `PANELS` in screen order; `panelForDigit` narrows `ctrl+<n>`; 5–8 reserved → no-op). **`focusStore`** (`src/input/focusStore.ts`): holds only `intendedId: FocusId` (= `PanelId | 'chat'`); the candidate set (`focusCandidates`) and the re-home invariant (`resolveFocus(intended, visible)` → chat if the intended panel is hidden) are **pure derived fns**, never stored — "focused on a hidden panel" is unrepresentable as *effective* focus, so the invariant is a theorem, not an imperative re-home. Geometry-driven `navigate(dir)` lives here over a `rects` registry. **keymap-as-data** (`src/input/keymap.ts`): a panel declares `Keymap<Intent> = {chord, intent, description}[]` + an `onIntent` handler (`PanelKeymap`); `chordMatches`/`matchKeymap` are pure. **Dispatcher** (`src/input/dispatcher.ts`): one pure `dispatchKey` in layered order — (1) global ctrl-chords win even while chat-focused (ctrl-only, so typing is safe; resolves the plan's apparent "chat-first" ordering by scoping the short-circuit to non-chord input — documented in-file), (2) chat short-circuit, (3) focused panel's declared keymap. Root `useInput` wiring in `src/hooks/useRootInput.ts` (`focusPanel` = show-then-focus; `spawn`/`toggleTmux` injectable for C13/C14, safe defaults). **`_directional_focus_target` ported** as pure `directionalFocusTarget(direction, current, candidates)` in `src/input/geometry.ts` (1:1 scoring tuple; unit-tested with hand-written rects). **Note for C5:** Ink's `measureElement` returns only `{width,height}` (no position); the absolute-rect bridge (`measureRect`, walks the Yoga `parentNode` chain) lives in `src/hooks/useInputStores.tsx` and the kernel stays pure over `Rect`. Demo: `src/components/FocusDemo.tsx` (throwaway) wires two dummy panels + chat; integration-tested with real simulated keys (`ctrl+l` nav, `ctrl+f` chat, hide-while-focused re-home, declared-intent fires only when focused). 47 new tests (geometry/panel/focus/keymap/dispatcher pure + the ink-testing-library integration), **102 total**; build/typecheck/lint/test green. **Contract surprise:** added `use-sync-external-store@^1.6.0` as a direct dep — `zustand/traditional` (the path C3's `useAppStore` already imports) needs it and it was missing; no C3 component test had exercised the React hook so it went unnoticed. C1/C2 bus files **unchanged** (verified: empty diff); C3 store/selector files untouched. | Rule 5 in code. Kills the "nothing highlighted" bug class and the central-gating smell. Future panels just declare a keymap. | C0 |
| **C5** ✅ **done** | **App shell + component vertical.** `App.tsx` composing top bar / bottom bar / left+right panel regions / chat input. The **component pattern**: `React.memo` + narrow selector (rule 1). One fully-worked reference **panel component** (e.g. plans list, two-line entries) consuming the C3 slice via a C4-registered keymap, rendered per spec. `ink-testing-library` component-test pattern. Top bar highlights toggled panels with subscript labels. *Landed:* chose **(a)** — the reference panel is built on the existing **C3 roster slice** (the `crows` panel, right region), not a new slice, per the plan's "prefer (a)" (the panel *pattern* is the deliverable and roster demonstrates it fully; adding a slice would expand scope). `src/components/RosterPanel.tsx` is THE reference: `React.memo` + narrow `useAppStore((s) => s.roster, shallow)` (rule 1), presentation via `useRosterView` (rule 2 — no sort/truncate in the component), **two-line entries** (line 1 name+status, line 2 harness · model), local cursor as `useState` (rule 1), keymap-as-data via `usePanelKeymap` with a typed-exhaustive `onIntent` (rule 5), focus highlight via `useEffectiveFocus`, rect via `useMeasureFocus`, and the bus reached only through the dispatched `actions.roster.refresh` (rule 3) — its file header carries the full "copy this to make panel X" recipe. `App.tsx` is the composition skeleton: `<AppStoreProvider>` + `<InputStoresProvider>` wrap, `Shell` runs `useRootInput()` **once**, and a `renderPanel(id)` map dispatches each visible panel to its component — `crows`→`RosterPanel`, the rest→`PlaceholderPanel` clearly tagged with the chunk that fills them (C6/C7/C9); a later chunk swaps one `case`, the shell is unchanged. Left region visible iff any of 1–4 on, right iff 9/0 on; chat input always visible (the focus home). **Top bar** (`TopBar.tsx` + `selectTopBar`) highlights toggled panels with real subscript glyphs (`plans₁ … crows₀`); **bottom bar** (`BottomBar.tsx` + `selectBottomBar`) shows global chords + the focused panel's declared keys, sourced from the keymap (rule 2 — bar formatting in selectors). `ink-testing-library` component-test idiom shipped: `test/components/RosterPanel.test.tsx` renders the panel against a `FakeBusClient`-backed store + C4 input stores inside both providers with the live `useRootInput`, asserts the two-line rows, focus highlight (effective-focus), and that a declared key (`j`) fires its intent only when focused; `test/App.test.tsx` asserts bars + always-visible chat + region visibility; `test/selectors/barSelectors.test.ts` covers the bar view-models. **Contract surprise (none on the bus):** C5 is the first chunk to mount `useRootInput` in a non-test entrypoint, exposing that Ink's `useInput` claims raw mode — added an `isRawModeSupported === true` guard to `useRootInput.ts` (the one C4 file touched, justified: the guard belongs there and was never exercisable before) so `npm run dev` over a non-TTY stdin renders + exits clean. **C1/C2 bus files unchanged** (empty diff); C3 store/selector files untouched; only `useRootInput.ts` of C4 touched. 12 new tests, **114 total**; build/typecheck/lint/test green, `npm run dev` renders the real shell and exits 0. | THE reference for every future panel component + its test. "Copy the plans panel" makes the notes panel. | C3, C4 |

**Gate to Phase B:** after C5, every layer has one tested reference (transport, slice, action,
selector, focus/keymap, component, plus their test idioms). An Opus reviewer confirms the five
rules hold and the references are copy-ready. Only then does Sonnet build-out begin.
**Gate result (2026-06-08): PASS** — Opus review found zero structural defects; all five rules
hold with file:line evidence; copy recipes traced and corrected (the `store.ts` "add slice X"
recipe now lists ≈5 edits incl. `initialAppState`, not 3).

---

### Phase A-bis — Shared overlay / transient-input-mode primitive (**Opus**; added 2026-06-08 after C6/C7)

**Why this was inserted mid-stream.** C8 (in-layout ticket editor), C12 (popup dialogs), and C14
(full-screen tmux view) each independently need a **transient input/focus mode**: a UI surface
rendered over (or in place of) the normal panels that **captures input exclusively** until
dismissed, then **restores prior focus**. C4 established *panel* focus but not *modal capture*.
Building it three times across parallel Sonnet chunks would breed three divergent modal patterns —
exactly the pollution the backbone phase exists to prevent. So one Opus chunk builds it first as a
tested, copy-ready reference; the modal-ish chunks then consume it. **This is the gate that unblocks
parallel build-out** (see the Phase B parallelization note).

| ID | Chunk | Pattern it anchors | Deps |
|----|-------|--------------------|------|
| **C7M** ✅ **done** | **Overlay & transient-input-mode primitive.** A `modeStore` (mode stack — each active mode carries an id, a *declared* keymap incl. its dismiss key, an `onIntent`, and a render) layered into the C4 dispatcher as a new **top layer (layer 0)**: while a mode is active it captures every key, routed to the active mode's keymap only; global chords and panel keymaps are suppressed unless the mode declares a pass-through. Entering a mode **saves the prior `focusedId`; exiting restores it** — one managed, derived transition, not scattered re-homing (mirrors the C4 re-home invariant). An `<Overlay>` render slot in `App.tsx` renders the active mode's component, supporting the three needed presentations **as data** (centered **modal**, **full-screen** takeover, **in-layout** region) without hardcoding one. Ships ONE tested reference mode (a minimal confirm/dismiss modal) + the `ink-testing-library` idiom for testing a mode (open → key → assert capture → dismiss → assert focus restored). Store layer stays framework-agnostic (rule 4); mode *state* is data, the render is a thin component. *Landed:* `src/input/modeStore.ts` — vanilla Zustand mode stack bound to `focusStore`; `enter(mode)` pushes + **saves effective focus** (idempotent re-enter of an id keeps its original saved focus), `exit(id?)` pops (top → restore saved focus; buried frame → remove without moving live focus). Focus save/restore is an **explicit** save-on-push/restore-on-pop contained to this transition (justified in-file: prior focus is real state with no derivation; a "derived" version would just store the same thing indirectly). **Dispatcher layer 0** (`dispatcher.ts`): `DispatchContext.activeMode: Mode \| null`; `DispatchOutcome` gains `{ layer:'mode'; handled:boolean }`; a live mode matches its keymap (handled), else **swallows** the key unless `passThrough===true` (then falls through to layers 1–3). Layered-dispatch doc comment updated so a later agent doesn't re-order it. `useRootInput` passes `selectActiveMode(modes)` per event. **`<Overlay>`** (`components/Overlay.tsx`): presentation-as-data — `modal` (centered Box over `useStdout` viewport), `fullscreen` (shell hides bars/panels via exported `presentationHidesLayout`), `inlayout` (renders inline, panels stay). Reference mode: `components/ConfirmModal.tsx` (`confirmMode(modes, {message,onChoose})` — y/n/Esc, self-dismissing). `modeStore` joined the input bundle (`createInputStores`/`useInputStores`, new `useModeStore` hook). **Recipe (C8/C12/C14):** declare `Mode` (`id`, `presentation`, `keymap`+`onIntent` that calls `exit(id)`, `render`) → `modes.getState().enter(yourMode(...))` → `<Overlay>` paints it. **C1/C2 bus files unchanged** (no `mode` refs in bus diff). C4/App files touched additively: `dispatcher.ts`, `useRootInput.ts`, `useInputStores.tsx`, `createInputStores.ts`, `App.tsx`. 163→**184** tests; build/typecheck/lint/test all green, `npm run dev` renders + exits 0. | THE transient-mode/overlay reference every modal-ish chunk copies — "copy the demo modal" makes the dialog (C12), the editor frame (C8), the tmux frame (C14). Kills divergent modal patterns before they start. | C4, C5 |

---

### Phase B — Feature build-out (**Sonnet** w/ Opus advisor; each names the reference to copy)

**Parallelization (post-C7M).** Once C7M lands, the next batch runs as **parallel Sonnet+advisor
agents in isolated git worktrees**: **C9** (right panels — no mode needed, pure panel-copy), **C8**
(editor — in-layout mode), **C12** (dialogs — modal mode), **C14** (tmux — full-screen mode). They
collide only on the *additive* regions of `store.ts` (slice wiring) and `App.tsx` (distinct
`renderPanel` / overlay cases); the manager merges those mechanical conflicts and re-verifies the
green gate before any dependent chunk (C10/C11/C13/C15) proceeds.

| ID | Chunk | Copy from | Deps |
|----|-------|-----------|------|
| **C6** ✅ **done** | Left panels: notes (2) + reports (3) lists. Delivered: `notesSlice`+`notesActions`+`notesSelectors`, `reportsSlice`+`reportsActions`+`reportsSelectors`; `NotesPanel`, `ReportsPanel`; wired in `store.ts`+`App.tsx`. **Contract gap:** `'report'` entity added to TS `protocol.ts` — not yet in Python `murder/bus/protocol.py`; Python side + PROTOCOL_VERSION must be bumped when service B13 lands. **Plans panel (1) remains a placeholder** — no chunk in Phase B is assigned to it; C7+ should confirm or assign. `store.test.ts` "unrelated entity" test updated (now uses `plan`/`ticket`/`escalation`; `note` is no longer unrelated). 114→138 tests; build/typecheck/lint/test all green. | C5 plans panel; C3 slice | C5 |
| **C7** ✅ **done** | Tickets panel (4): 2-row × 5-column layout (each `X/Y` group is one `flexDirection="column"` box with top=X, bottom=Y), alternating background via `rowParity` from selector, **deps cell** rendering `pending_dep_ids` (`'ok'` or joined ids), `depsSatisfied: boolean` from selector (no string-matching in component — rule 2 hardening). Delivered: `ticketsSlice` + `ticketsActions` + `ticketsSelectors`; `TicketsPanel`; wired in `store.ts` + `App.tsx`. **Contract gaps:** (1) `plan` and `worktree` absent from `ScheduleTicketRow` wire DTO — both cells render `'—'` until B13 adds them; (2) RPC `ticket.get_snapshot` modeled on Python `get_schedule_snapshot()` but not yet live on bus. `store.test.ts` "unrelated entity" test updated (was `ticket` → now `queue_row`; `ticket` is now wired to tickets slice). 138→163 tests; build/typecheck/lint/test all green. | C5 panel + C3 slice (richer view-model) | C5 |
| **C8** ✅ **done** | Ticket editor (enter on row): body view, checklist `[ ]`↔`[x]`, free-form schedule input via backend `parse_duration()`. **Vim-emulator package selection spike** lives here. Editor is an **in-layout C7M mode** (surrounding panels stay visible — no `$EDITOR`-blank). *Landed:* `ticketDetailSlice` + `ticketDetailActions` (3 modeled RPCs: `ticket.get_detail`, `ticket.save_body`, `ticket.schedule`); `TicketEditorMode.tsx` (custom minimal vim editor — `ink-text-input` single-line only; `ink-editor` DOM-incompatible; `@inkjs/ui` has no editor; all three evaluated, none suitable, custom fallback per plan sanction); `TicketsPanel` `'open'` intent on enter key. **Capture proof:** layer 0 swallows `ctrl+f` while editor active — `intendedId` stays `'tickets'` (not flipped to `'chat'`). **No model-override UI:** spec item "remove empty no-model-override option" — no editable model picker exists in C8 surface; model is display-only frontmatter in editor header. Belongs to C13/spawn-wizard. **Contract gap:** `ticket.get_detail`, `ticket.save_body`, `ticket.schedule` not yet live on bus (B13); body format uses `# Checklist` with `[ ]`/`[x]` lines — confirm at B13. **Inlayout position:** editor renders at `<Overlay>` slot (bottom of Shell), below panels, not beside focused panel — surrounding panels stay visible above as required. Position is shell-determined, not editor-determined. 224→**224** tests (40 new); build/typecheck/lint/test all green. | **C7M** (in-layout mode) + C7 tickets row/keymap; becomes the reference for plan/note editors | C7, **C7M** |
| **C9** ✅ **done** | Right panels: crows-by-type (collaborator→planners→rogues→ticket), minimized + maximized; usage (9) right-aligned left of crows. Delivered: `crowsSelectors.ts` (type-grouping + ordering in selector — rule 2 proof), `CrowsPanel.tsx` (minimized/maximized toggle via `useState` + `'m'` keymap intent), `usageSlice`+`usageActions`+`usageSelectors`, `UsagePanel.tsx`; wired in `store.ts` (5 additive edits) + `App.tsx` (swapped `'crows'` and `'usage'` placeholder cases). **RosterRow** gains `role: string` (wire-faithful, raw — C10 narrows this for discriminated-union agent identity). **Contract gaps:** `usage.get_snapshot` modeled but not live — Python usage data currently embedded in `ScheduleSnapshot.usage_gauges`; dedicated RPC to be confirmed when B13 lands. Usage invalidation keys on `'agent'` entity (no dedicated `'usage'` entity in Python protocol). 184→**228** tests; build/typecheck/lint/test all green. | C5 panel + C3 slice | C5 |
| **C10** ✅ **done** | Crow chat panes + chat routing via **discriminated-union agent identity** (no conversation-id string parsing). `agent.message` action. *Landed:* `agentIdentity.ts` (discriminated union `CollaboratorIdentity \| PlannerIdentity \| RogueIdentity \| TicketIdentity`, derived from `role`+`ticketId` — zero string parsing, anti-pattern proof in tests); `conversationsSlice.ts` (own-shape, NOT factory — event-driven append/update keyed by `agentId`; modeled after `ticketDetail` as a hand-written slice); `conversationsActions.ts` (`applyBlock` for `block-appended`/`block-updated`, `send` = sole caller of `agent.message` RPC routed by `agentId`, `setActivePaneAgentId`); `conversationsSelectors.ts` (`formatBlock`, `selectConversationTurns`, `selectFavoritesChatPanes` — collaborator+rogue default-favorited, ordered by spec, `selectActiveAgentId` with user-pin priority); `CrowChatPanel.tsx` (memo, `CrowChatPane` per favorited crow, last-20 turns, speaker-colored); wired in `store.ts` (second `bus.subscribe` for `conversation.block` events, both-disposer teardown) and **mounted in `App.tsx`** below `CrowsPanel` in the `'crows'` case. **App-path test added** in `App.test.tsx` proving CrowChatPanel is mounted (not just isolated). **Seams:** (1) `activePaneAgentId` slot for C11 starring; (2) `ChatInput` text-editor char-capture (C7M `onUncaptured` pattern) deferred — `agent.message` action is delivered and tested; UI buffer wiring needs a persistent chat mode, not in scope per C5 docstring; documented in `ChatInput.tsx`. Exploratory tile layout (`ctrl+h/l`) skipped per plan footnote. 228→**354** tests; build/typecheck/lint/test all green. | C5 panel + C3 slice + C9 roster identity | C9 |
| **C11** ✅ **done** | Starring + document toggling (generalized via prefs RPC `tui.save_favorites`); parent-plan indentation (4-space, child recency bubbles parent); **+ folded in: `ctrl+s` context reconciliation (A), cursor-accessibility decision (B), chat-input send (F)**. *Landed:* **(A) `ctrl+s` dual-purpose** — `dispatchGlobalChord` claims `ctrl+s` (→ spawn wizard) ONLY when chat is focused; with a panel focused it returns `false` and falls through to layer 3, where the focused panel's declared `ctrl+s → star` keymap stars its own highlighted row. Documented as the one deliberate exception to "global chords always win". **(B) cursor-accessibility = option (a)** (advisor-vetted before writing): each panel keeps its cursor as local `useState` (rule 1 preserved) and resolves its own highlighted row in its `star`/`open` intent — no cursor lifted to a shared store. `deriveSpawnContext` no longer uses C13's first-row proxy: the **focused doc is the open doc-view** (`docView.open`), the cleanest reading of "focused-doc-wins" that needs no lifted cursor (advisor's clinching point: when `ctrl+s` spawns, chat is focused, so there's no live list cursor — the remembered open doc is the only coherent "focused doc"). **(C) generalized starring** — hand-written `favorites` slice (`Set<string>` + load/save lifecycle, NOT the listSlice factory) + `favoritesActions` (`tui.load_favorites`/`tui.save_favorites` via `declare module`, **modeled-not-live**, optimistic local-first writes) + `favoritesSelectors` (`isFavorited` ORs explicit set with `isDefaultFavorited`; `stableSortStarredFirst` re-partition). Notes/Reports/Plans/Crows all star via `ctrl+s`; starred sort to top in each selector (rule 2). `selectFavoritesChatPanes`/`selectActiveAgentId` made prefs-aware; crow `ctrl+s` also keeps the pane active (`setActivePaneAgentId`). **(D) doc-view** — `docView` slice + actions (`doc.get`, **modeled-not-live**, stale-reply-guarded) + `DocViewMode` (read-only **inlayout C7M mode** copying `TicketEditorMode`'s shape, scroll-only); `useDocView(kind)` toggles open/minimize; `enter` on a shown doc minimises + restores focus, `enter` again re-opens. **(E) parent-plan indentation** — new `plans` slice (listSlice factory + `parent` field) + actions (`plan.get_snapshot`, **modeled-not-live**) + `plansSelectors` reconciling THREE orderings in stated precedence (starred-block → effective-recency → tree-flatten → child star/recency); `PlansPanel` replaces the last placeholder. **(F) chat-input send = persistent input mode** — NOT a modeStore frame (chat is the permanent home, nothing to restore); a tiny `chatInputStore` buffer + a layer-2 `ChatInputHandler` (built in `Shell`, injected to `useRootInput`): printable chars buffer, Enter sends via `conversations.send` to `selectActiveAgentId` then clears; layer-1 ctrl-chords still preempt it so global chords fire while typing. `ChatInput` renders the live buffer via `TextInput`. **Still exactly ONE `useInput`** (`useRootInput`) — verified. **Modeled-not-live RPCs (all `declare module`, B13):** `tui.load_favorites`, `tui.save_favorites`, `plan.get_snapshot`, `doc.get`. **C1/C2 bus files byte-identical** (verified empty diff). 375→**405** tests; build/typecheck/lint/test all green; `npm run dev` renders the shell (live chat input) + exits 0. **For C15:** `plans` panel is now real (no placeholders remain); the persistent-chat-mode + dual-purpose-`ctrl+s` + open-doc-as-focused-doc patterns are the copyable references. | C3 action + C6/C9 | C6, C9 · svc B13 (prefs) |
| **C12** ✅ **done** | Dialogs: `ctrl+p` new-plan popup, `ctrl+t` new-ticket popup — each a **modal C7M mode**. Delivers `newPlanMode` (two fields: plan name + message) and `newTicketMode` (single title field). Introduces `onUncaptured` dispatcher extension (C12 innovation) for raw-char capture in text-input modals — shared primitive C8 and C13 copy. Hand-rolled `TextInput` component (no external dep). Four RPCs declared in `dialogActions.ts` (`plan.create`, `ticket.quick_create`, `ticket.next_id`, `ticket.exists`) flagged **B13 — not live**. `ctrl+p`/`ctrl+t` added to `GlobalHandlers`, `dispatchGlobalChord`, and `DeferredGlobalHandlers` (additive only; C14 merge is clean). Layer-0 doc in `dispatcher.ts` updated to document `onUncaptured`. Submit path: exit-then-act + `.catch()`. 184→**206** tests; build/typecheck/lint/test all green. | **C7M** (modal mode) + C4 chords; dialog becomes the reference for C13 | C4, **C7M** · svc B13 (`ticket.quick_create`/`next_id`/`exists`) |
| **C13** ✅ **done** | Spawn wizard (`ctrl+s`): collects **effort** (j/k selection), optional spawn-context step (focused `notes`/`reports` panel → reference-by-path kickoff_message `"Please read .murder/<dir>/<name>.md before starting."`), fires `crow.spawn_rogue {effort, kickoff_message?}` (declared via `declare module` in `spawnActions.ts` — C1/C2 bus files untouched). `spawnWizardMode` factory follows C12 mutable-closure + `refresh()` pattern; modal presentation (`passThrough` unset) for exclusive capture. **No second `useInput`** — wizard is selection-only (j/k + y/n keymap), `onUncaptured` not needed. `deriveSpawnContext` uses first-row proxy for cursor (C11 seam: update when cursor-in-store lands). **ctrl+s overload note:** C13 takes the `spawn` handler slot; C11 also wants `ctrl+s` for starring — manager must reconcile when C11 lands. `crow.spawn_rogue` not yet live (B10 carries effort end-to-end; confirm wire shape at B13 review). 321→**321** tests (19 new: 13 wizard + 6 `deriveSpawnContext` units); build/typecheck/lint/test all green. | C12 dialog pattern; C3 action | C12 |
| **C14** ✅ **done** | `ctrl+y` tmux/parsed toggle: a **full-screen C7M mode** that opens the tmux-frame subscription **only while active**, renders one ANSI frame, and closes it on return. Delivered: `TmuxMode.tsx` (`tmuxMode` factory + `TmuxFrame` component with `useEffect` subscription lifecycle), `useBusClient.ts` (thin `BusClient` context for transient streaming subscriptions), wired `toggleTmux` in `useRootInput.ts`, `BusClientProvider` added to `App.tsx`. **Subscription lifecycle:** opened in `useEffect` on component mount, closed in cleanup on unmount — every exit path (ctrl+y, Escape, or any `exit(TMUX_MODE_ID)`) unmounts `TmuxFrame` and closes the subscription; no leak possible. **`passThrough: true`** on the mode lets ctrl+y fall through from layer 0 to the global-chord layer so the second press exits. **Contract gap:** `TmuxFrameEvent` (`tmux.frame`) is a speculative forward-declaration not yet in Python `murder/bus/protocol.py`; no `PROTOCOL_VERSION` bump until the Python side lands. **Pane-scoping note for service:** no `pane_id` filter field exists yet; flagged for service confirmation. 184→**190** tests; build/typecheck/lint/test all green. | **C7M** (full-screen mode) + C4 chord + C1 subscription | C4, **C7M** |
| **C15** | ⏳ **audit done; retirement deferred** | Retire the Textual app; parity pass against this spec; confirm the store/action/selector layer is reusable by a future web/phone client (no Ink imports leak below the component layer). | — | C6–C14 |

**C15 audit (2026-06-08, manager — non-destructive; Textual NOT retired):**
- **Reusability invariant — PASS.** `store/`, `selectors/`, `bus/` import no `ink` at all (the
  reusable core). The only sub-component `ink` refs are `import type { Key }` in
  `input/keymap.ts` + `input/dispatcher.ts` — **type-only, compile-erased**, no runtime leak. For
  a future DOM client, abstract `Key` out of `input/` (minor, already flagged by the C7M review).
- **Spec parity — complete.** All panels (plans/notes/reports/tickets, usage, crows-by-type),
  ticket editor + checklist + schedule, starring + doc-toggle + parent-indent, crow chat +
  discriminated-union routing, chat-send, dialogs (`ctrl+p`/`ctrl+t`), spawn wizard, `ctrl+y`
  tmux, focus manager + keymap-as-data, bars, overlay/mode primitive. **No `PlaceholderPanel`
  mounted; rule 5 holds (one `useInput`); 405 tests green.**
- **Retirement deferred (deliberate):** the whole TUI runs against `FakeBusClient`; the live RPC
  surface (the 13 `declare module` augmentations) + events (`state.snapshot` keys,
  `conversation.block`, `tmux.frame`) do **not** exist on the bus yet (service B13). Deleting the
  working Textual app now would leave no TUI able to reach the real backend. **Retire only after
  service B13 lands + a live smoke test, with user go-ahead.**
- **Cleanup candidates (left untouched — minor):** `components/FocusDemo.tsx` (C4 throwaway demo)
  and `components/PlaceholderPanel.tsx` (no longer mounted) can be removed once nothing imports
  them in tests.

### Open follow-ups (blocking real use — all on [[newui-service]] B13)
1. Implement the modeled RPCs on the bus: `crow.get_snapshot`, `usage.get_snapshot`,
   `ticket.get_snapshot`/`get_detail`/`save_body`/`schedule`, `note.get_snapshot`,
   `report.get_snapshot`, `plan.get_snapshot`, `doc.get`, `tui.load_favorites`/`save_favorites`,
   `plan.create`, `ticket.quick_create`/`next_id`/`exists`, `agent.message`, `crow.spawn_rogue`
   (effort). Confirm names/shapes against the TS `declare module` decls in lockstep.
2. **Protocol forward-decls need the Python side + a `PROTOCOL_VERSION` bump in lockstep:**
   `report` entity (C6) and the `tmux.frame` event (C14) were added TS-side only.
3. Emit the key-only `state.snapshot` events for each entity the slices invalidate on, plus the
   `conversation.block` and `tmux.frame` streams.

Vim-emulator selection was the C8 spike (custom minimal editor — no suitable package). Crow
chat-tile big/small layout (`ctrl+h/l`) remains exploratory (skipped in C10).
