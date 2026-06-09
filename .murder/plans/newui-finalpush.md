---
created_at: '2026-06-08T00:00:00'
name: newui-finalpush
parent: newui
related_plans: [newui, newui-service, newui-inktui, plan-tui-data-render-split]
status: draft
phase: null
---

# New UI — Final Push (make `murder` launch the live Ink TUI, retire Textual)

The closing plan of the `newui` rewrite. The Ink TUI ([[newui-inktui]]) is built (C0–C15,
405 tests) but runs against `FakeBusClient` and unmounts on first paint; the renderer-agnostic
backend ([[newui-service]]) landed its write-RPC/V-list closure (C14). **What remains is the
wiring that makes a real `murder` launch the Ink TUI against live, self-updating data, plus the
salvage ports the rewrite missed, then retiring Textual.** This document is the handoff for that
phase.

Status: spec being authored 2026-06-08. **F1 (key-only event uniformity) is the keystone and the
top priority** — nothing else makes the TUI *live* until it lands.

---

## The keystone problem (read this first)

A TUI must both pull data and **know when to pull it again**. The two halves disagree on the
second part, and that disagreement is the single thing keeping the Ink TUI at "renders once with
fake data."

- **What Ink consumes.** The store opens two bus subscriptions at startup (`inktui/src/store/store.ts`):
  `conversation.block` (content-bearing transcript updates — **already live** ✓) and
  `state.snapshot` — a **key-only** event whose payload is just the name of the `Entity` that
  changed. Ink's fixed invalidation table maps `agent→roster, plan→plans, note→notes,
  report→reports, ticket→tickets, queue_row→usage`; on each event it re-pulls that one slice's
  snapshot RPC. **There is no polling loop (locked architecture).** If the `state.snapshot` event
  for an entity never arrives, that panel never updates after first paint.
- **What the service emits.** The service pushes *rich, typed* events (`StatusChangeEvent`,
  `SchedulerDecisionEvent`, `ConversationBlockEvent`, …) and emits the key-only `StateSnapshotEvent`
  **for `ESCALATION` only** (`murder/runtime/workers/state_worker.py`). The old Textual TUI copes
  by subscribing to each typed event and mapping it to a refetch (its coordinator); Ink does not —
  it only listens for the generic `state.snapshot`.
- **Result:** the service never tells Ink "the ticket slice changed" in the vocabulary Ink listens
  for, so live data stops at first paint.

### Decision (locked 2026-06-08): uniformize on **key-only**, one format end to end

The **bus→client contract becomes key-only `state.snapshot{entity}` only.** We do *not* build a
translation layer and do *not* maintain two client-facing event formats. Rationale (per the
five-axis comparison): key-only is the cheapest to extend (new entity = one enum member + one
table row), the least work to standardize (Ink already speaks it; the service only adds tiny
no-payload emits), and the only format that scales cleanly to more functionality **and** more
client types (web/mobile inherit the store + RPC snapshots with zero per-event-type parsing).
Content-bearing events stay only as a deliberate exception for genuinely high-rate streams
(`conversation.block`). The typed events may remain **internal** service implementation detail,
but they leave the client contract.

**Low-bandwidth forward-compat:** the `state.snapshot` envelope **reserves one optional `payload`
field** (unused now). Default behaviour is key-only (tiny push, client refetches); a future
low-bandwidth mode can inline the changed data on that field to kill the refetch round-trip,
paired with field-masked/partial snapshot RPCs. This is a superset of what Ink already consumes —
no second format, no migration.

### Where the keystone work lives

| Side | File | Change |
|---|---|---|
| Producer (protocol) | `murder/bus/protocol.py` | add `Entity.REPORT`; bump `PROTOCOL_VERSION`; add optional `payload` field to the snapshot envelope (documented, unused) |
| Producer (emit) | `runtime/workers/state_worker.py` + each read-model mutation site (`runtime/orchestration/orchestrator.py`, `runtime/scheduler/worker.py`, `app/service/filesystem_sync.py` + `work/{tickets,plans,notes}/sync.py`, completion/outcome paths) | emit key-only `StateSnapshotEvent{entity}` for `agent/plan/note/report/ticket/queue_row` on change |
| Consumer (protocol) | `inktui/src/bus/protocol.ts` | drop the `report`/`tmux.frame` forward-decls; mirror the Python enum + version |
| Consumer | `inktui/src/store/store.ts` | invalidation table already correct — verify it matches the final enum |
| Reference (to delete later) | `murder/app/tui/.../coordinator.py` | the typed-event→refetch mapping Ink replaces |

---

## Other decisions already made

- **RPC naming → align Ink to the live names** (locked). The read RPCs already exist as
  `state.crow_snapshot`, `state.schedule_snapshot`, `state.notes_snapshot`, `state.plans_snapshot`,
  `state.reports_snapshot`, `state.ticket_detail`, `state.{plan,note,report}_display`. We edit the
  Ink `declare module` decls + action method strings to these names rather than churning the
  backend. Cheapest, zero backend risk, Textual stays working until deleted.
- **Node is acceptable as a runtime dependency.** Dev runs `tsx src/index.tsx`; the distributed
  build ships the compiled `inktui/dist` (hatch include) and `murder` runs `node dist/index.js`.
  Clear error if Node is absent.
- **Execution shape — TBD** (open decision below). Default expectation: parallel Sonnet+advisor
  worktree chunks, manager-merged, mostly-linear queue — same as the inktui build — unless the user
  picks otherwise.

---

## Salvage findings (from the 2026-06-08 scans of Textual vs Ink)

### RPC surface — most "missing" reads already exist
- **Already live, exact match (no work):** `ticket.next_id`, `ticket.exists`,
  `tui.load_favorites`, `tui.save_favorites`.
- **Exist, renamed (F2 — align Ink names; fix 3 shapes):** the snapshot family above. Shape
  mismatches: usage is **embedded in the schedule snapshot** (not a separate RPC); `doc.get`
  returns a `DisplaySnapshot` with metadata, not bare `{body}`; tickets arrive as 3 buckets.
- **Exist via command system (F2 — redirect Ink to `command.submit(kind=…)`):**
  `ticket.quick_create`, `crow.spawn_rogue`, `agent.message`.
- **Genuinely missing (F3 — build):** `ticket.save_body`, `ticket.schedule`, `plan.create`.

### Feature logic the Ink rewrite missed
| What | Textual source | Recommendation |
|---|---|---|
| Crow health classification (border color from status/escalation/60s stuck-heartbeat) | `app/tui/crow_health.py:34-66` | **Port** → Ink selector |
| Roster filtering (hide done/dead crows; drop failed agents stale >2h) | `app/tui/stores/roster.py:199-225` | **Move to service** → fold into `crow.get_snapshot` read model (it's a liveness determination; Ink currently shows stale crows) |
| Clipboard image read (Wayland `wl-paste` / X11 `xclip`) | `app/tui/clipboard_image.py:9-63` | **Port** → Node subprocess; must stay client-side (TUI owns the tty) |
| Conversation segment formatting for `agent_event` + `choice_prompt` (incl. live-prompt trailing-segment heuristic) | `app/tui/crows_view.py:464-544,595-608` | **Port** → `conversationsSelectors.ts` (Ink covers the other 5 block types) |
| Note-capture chords (ESC double-tap 0.45s, ESC-then-d delete, blur timer, undo) | `app/tui/note_capture.py:109-247` | **Verify** the Ink dispatcher can express the timer/double-tap FSM before porting; flag if it needs a dispatcher extension |
| Usage gauges, roster labels, crow grouping | — | **Already covered** in Ink selectors (no action) |

---

## Work breakdown (chunks)

Ordered so the keystone lands first. `F2`/`F3`/`F4` are backend-ish and parallel-safe against each
other once `F1`'s protocol change is in; `F5`–`F7` are Ink-side; `F8` is last and gated on a live
smoke test.

- [ ] **F1 — Key-only event uniformity (KEYSTONE).** Add `Entity.REPORT` + bump
  `PROTOCOL_VERSION` + reserve the optional `payload` field (`murder/bus/protocol.py`); mirror in
  `inktui/src/bus/protocol.ts` (drop forward-decls). Emit key-only `StateSnapshotEvent{entity}` for
  `agent/plan/note/report/ticket/queue_row` at every read-model mutation site. **First step: a
  coverage audit** — list each mutation site and the entity it must emit, so none is missed.
  *Done when:* mutating any domain emits the matching key-only event; the Ink invalidation table
  maps 1:1 to the enum; the bus→client contract carries no typed domain events; tests assert each
  site emits the right key. *Deps: none.* **Top priority.**

- [ ] **F2 — RPC name/shape reconciliation (Ink-side).** Align the Ink `declare module` names +
  action method strings to the live `state.*_snapshot` names. Fix the 3 shapes (usage reads the
  schedule snapshot's gauges; `doc.get`→`DisplaySnapshot`; ticket 3-bucket). Redirect
  `ticket.quick_create` / `crow.spawn_rogue` / `agent.message` to `command.submit(kind=…)`.
  *Done when:* every Ink action calls a live method; no `*.get_snapshot` name without a server
  handler remains. *Deps: F1 protocol (enum/version).*

- [ ] **F3 — Build the 3 missing RPCs.** `ticket.save_body`, `ticket.schedule` (orchestrator
  command worker + `reconcile_ticket_md` / scheduling), `plan.create` (verify against existing plan
  scaffolding — may be a thin wrap). *Done when:* the ticket editor saves body + schedule, and the
  new-plan modal creates a plan, end to end. *Deps: F1.*

- [ ] **F4 — Move roster filtering to the service.** Fold Textual's done/dead/stale-failed
  predicates into the `crow.get_snapshot` read model so the wire roster excludes them. *Done when:*
  Ink shows no stale/dead crows without any client-side filtering. *Deps: F1.*

- [ ] **F5 — Ink live runner.** Replace the smoke-only `index.tsx` (immediate unmount) with a
  standing input loop; swap `FakeBusClient`→`UdsBusClient`; inject the socket path (proposed:
  `MURDER_BUS_SOCKET` env var — confirm). *Done when:* `node dist/index.js` against a running
  service holds the shell and renders live data. *Deps: F1, F2.*

- [ ] **F6 — `murder` launch path.** In `app/cli/tui_cmd.py:_launch_tui()`, after
  `_ensure_supervisor()` brings the daemon up, spawn the Node Ink process against the socket
  instead of running `MurderApp` in-process; handle teardown. Dev: `tsx`; distribution: hatch
  include `inktui/dist` + `node dist/index.js`; clear error if Node missing. *Done when:* bare
  `murder` launches the Ink TUI on live data. *Deps: F5.*

- [ ] **F7 — Salvage ports (Ink).** Port crow-health classification → selector; `agent_event` +
  `choice_prompt` formatting → `conversationsSelectors`; clipboard image read → Node subprocess.
  **Verify** the note-capture ESC chord FSM is expressible in the dispatcher (flag if it needs an
  extension) before porting. *Done when:* the four ports land with tests; the FSM verdict is
  recorded. *Deps: F2 (selectors against live shapes).*

- [ ] **F8 — Live smoke test + retire Textual.** With a real `murder` launch showing live,
  updating data, delete `murder/app/tui/` + its tests and the unmounted scaffolding
  (`inktui/src/components/{FocusDemo,PlaceholderPanel}.tsx`). Git preserves Textual for recovery.
  *Done when:* Textual is gone, the suite is green, and `murder` runs the Ink TUI. *Deps: all.*

---

## Per-chunk contract (every chunk)
- Re-read the relevant section here before starting; this plan is the source of truth.
- Implement only the listed scope; if scope is wrong, stop and note it here rather than expanding.
- Add/adjust tests for the behaviour changed; run the suite; leave it green.
- Commit on `ink-rewrite` naming the chunk id (e.g. `feat(finalpush): F1 …`).
- Tick the box and append a one-line `done: <sha>, <notes>` before handing off.

## Open decisions (resolve before/at kickoff)
1. **Execution shape** — parallel worktree chunks (manager-merged) vs strict linear queue vs
   manager drives directly. (Default: parallel, mostly-linear queue.)
2. **Socket handoff to Node — RESOLVED.** The socket is **per-project, deterministic, no
   override**: `socket_path_for_repo(repo_root)` (`murder/state/storage/service_registry.py:57`) =
   `service_runtime_root()` (`$XDG_RUNTIME_DIR/murder` or `/tmp/murder-$UID`, lines 35-39) /
   `project_session_name(repo_root)` (basename + `sha256(resolved_repo_path)[:12]`, lines 42-54) /
   `SOCKET_BASENAME` (`bus.sock`, `murder/bus/protocol.py:465`). There is **no** `MURDER_BUS_SOCKET`
   env / `--socket` flag today. Decision: the Python launcher (`_launch_tui()`, which already calls
   `default_socket_path(repo)`) **passes the resolved absolute path to Node via the
   `MURDER_BUS_SOCKET` env var** — the TS side must NOT reimplement the per-project hash; Node's
   `UdsBusClient` just connects to the given path. (F5/F6.)
3. **F1 coverage audit output** — the concrete mutation-site→entity list (produced as F1's first
   step; record it here so it's auditable).

---

## F1 coverage audit (mutation-site → entity map)

**Snapshot inputs (pass 1).** Each row below is a writer of data read by the listed RPC.

| Snapshot RPC | Primary inputs |
|---|---|
| `state.crow_snapshot` | `agents` (+ `tickets` join for title/harness/model/ticket_status), `escalations` (open count per ticket) |
| `state.schedule_snapshot` | `scheduler_state`, `scheduler_decision_cache`, `tickets` + `ticket_deps`, `harness_usage_snapshots`, `agents` (running) + `tickets` join, `tickets.schedule_at` |
| `state.plans_snapshot` | `plans` (excl. superseded), `plan_revisions` (count), `agent_messages` (`planner-{name}` ordering) |
| `state.notes_snapshot` | `notes` (`status='active'`) |
| `state.reports_snapshot` | filesystem `.murder/agents/reports/*.md` (mtime/size) |

| Mutation site (file:line) | What it changes | Entity to emit | Existing typed event here? (which / none) | Notes (hook point, edge cases) |
|---|---|---|---|---|
| **AGENT (`state.crow_snapshot`)** | | | | |
| `murder/app/service/runtime.py:162` | `agents` upsert via `sync_agent` → `upsert_agent` | `agent` | none | Central hook: every `register_agent` / status change funnels here. Covers spawn, stop, reap side-effects. |
| `murder/runtime/agents/runner.py:114` | registers agent → `sync_agent` on spawn | `agent` | none | Collaborator / crow / planner spawn path. |
| `murder/runtime/orchestration/orchestrator.py:214` | `agents` INSERT failed crow stub | `agent` | none | Kickoff failure path before `_fail_ticket`. |
| `murder/runtime/orchestration/orchestrator.py:416` | `register_agent` crow_handler → `sync_agent` | `agent` | `StatusChangeEvent` at `crow_handler.py:92` | Handler start publishes typed agent event; no `StateSnapshotEvent`. |
| `murder/runtime/orchestration/orchestrator.py:436` | `register_agent` planning_handler | `agent` | `StatusChangeEvent` at `planning_handler.py:86` | |
| `murder/runtime/orchestration/orchestrator.py:499` | `register_agent` rogue crow | `agent` | none | Rogue bypasses `CrowAgent.start()`; only `sync_agent` at `:515`. |
| `murder/runtime/orchestration/orchestrator.py:515` | `agents` upsert rogue running | `agent` | none | |
| `murder/runtime/orchestration/orchestrator.py:823` | `agents.status` → dead (force-stop unregistered) | `agent` | none | Also affects `schedule_snapshot.running_agents` → consider co-emit `ticket` if ticket-linked. |
| `murder/runtime/orchestration/orchestrator.py:869` | `rename_agent` DB rekey rogue | `agent` | none | In-memory rename at `:853`; DB at `:869`. |
| `murder/runtime/orchestration/orchestrator.py:997` | `agents.status` → dead (plan deprecate) | `agent` | none | Planner/handler IDs for deprecated plan. |
| `murder/runtime/orchestration/orchestrator.py:1058` | `rename_agent` DB rekey planner | `agent` | none | Plan rename runtime retarget. |
| `murder/runtime/orchestration/orchestrator.py:1064` | `rename_agent` DB rekey planning_handler | `agent` | none | |
| `murder/runtime/orchestration/orchestrator.py:1071` | `sync_agent` planner after rename | `agent` | none | |
| `murder/runtime/orchestration/orchestrator.py:1073` | `sync_agent` handler after rename | `agent` | none | |
| `murder/runtime/orchestration/orchestrator.py:1093` | `agents.status` → dead (orphan collaborator row) | `agent` | none | Pre-spawn cleanup. |
| `murder/runtime/agents/crow.py:70` | `sync_agent` failed harness attach | `agent` | none | |
| `murder/runtime/agents/crow.py:81` | `sync_agent` failed prompt paste | `agent` | none | |
| `murder/runtime/agents/crow.py:87` | `sync_agent` running + publish | `agent` | `StatusChangeEvent` at `:89` | |
| `murder/runtime/agents/crow.py:116` | `sync_agent` on stop | `agent` | none | Status done/failed; may affect `running_agents`. |
| `murder/runtime/agents/crow_handler.py:90` | `sync_agent` handler running | `agent` | `StatusChangeEvent` at `:92` | |
| `murder/runtime/agents/crow_handler.py:303` | `agents.last_heartbeat_at` (`heartbeat_agent`) | `agent` | `HeartbeatEvent` at `:274` (stuck only) | **High-rate.** Updates `last_seen` in crow snapshot. Coalesce/debounce or emit on status/session change only. |
| `murder/runtime/agents/crow_handler.py:344` | `sync_agent` failed handler | `agent` | `StatusChangeEvent` at `:347`, `ErrorEvent` at `:360` | |
| `murder/runtime/agents/crow_handler.py:377` | `sync_agent` after tick failure finalize | `agent` | none | |
| `murder/runtime/agents/collaborator.py:102` | `sync_agent` running | `agent` | `StatusChangeEvent` at `:104` | |
| `murder/runtime/agents/collaborator.py:124` | `sync_agent` failed startup | `agent` | none (notice via `ConversationBlockEvent`) | |
| `murder/runtime/agents/collaborator.py:135` | `sync_agent` on stop | `agent` | none | |
| `murder/runtime/agents/planning_agent.py:69` | `sync_agent` failed start | `agent` | none | |
| `murder/runtime/agents/planning_agent.py:77` | `sync_agent` failed prompt | `agent` | none | |
| `murder/runtime/agents/planning_agent.py:83` | `sync_agent` running | `agent` | `StatusChangeEvent` at `:85` | |
| `murder/runtime/agents/planning_agent.py:111` | `sync_agent` on stop | `agent` | none | |
| `murder/runtime/agents/planning_handler.py:84` | `sync_agent` handler running | `agent` | `StatusChangeEvent` at `:86` | |
| `murder/runtime/agents/base.py:356` | `sync_agent` daemon stop | `agent` | none | Crow/planning handlers on stop. |
| `murder/app/service/agent_registry.py:90` | `set_agent_status` → dead via `reap` | `agent` | none | Called from `Runtime.reap` / orchestrator stop paths. |
| `murder/app/service/recovery.py:73` | `agents.status` → dead (startup reconcile) | `agent` | none | Zombie tmux cleanup at service start. |
| `murder/runtime/workers/done_session_sweeper.py:51` | `agents.session` NULL (`clear_agent_session`) | `agent` | none | Stale crow session sweep; agent row remains. |
| `murder/state/persistence/agents.py:32` | `agents` INSERT (`upsert_agent` insert path) | `agent` | none | DAO — callers above are hook points. |
| `murder/state/persistence/agents.py:55` | `agents` UPDATE (`upsert_agent` update path) | `agent` | none | |
| `murder/state/persistence/agents.py:84` | `agents.last_heartbeat_at` | `agent` | see `crow_handler.py:303` | |
| `murder/state/persistence/agents.py:91` | `agents.status` + heartbeat | `agent` | none | Used by reap/recovery/orchestrator force-stop. |
| `murder/state/persistence/agents.py:114` | `agents` rekey + `agent_messages` rekey | `agent` | none | `rename_agent` persistence. |
| `murder/state/persistence/agents.py:217` | `agents.session` NULL | `agent` | none | |
| `murder/verdict/escalations/service.py:47` | `escalations` INSERT (user) | `agent` | `EscalationEvent` at `:109` | Open escalation count on crow rows; also `ESCALATION` (already emitted for ack/create only today). |
| `murder/verdict/escalations/service.py:67` | `escalations` INSERT (collaborator) + body file | `agent` | `EscalationEvent` at `:109` | |
| `murder/verdict/escalations/service.py:76` | `escalations.body_path` UPDATE | `agent` | none | After markdown body write. |
| `murder/runtime/workers/state_worker.py:40` | `escalations` resolve | `agent` | `StateSnapshotEvent{ESCALATION}` at `:42` | Only entity with key-only emit today. Crow open-esc count changes too. |
| `murder/runtime/workers/state_worker.py:67` | `escalations` INSERT (RPC) | `agent` | `StateSnapshotEvent{ESCALATION}` at `:77` | |
| `murder/verdict/escalations/views.py:65` | `escalations` resolve (CLI `ack_escalation_db`) | `agent` | none | Offline CLI path; no bus. |
| `murder/verdict/escalations/model.py:23` | `escalations` INSERT (`queue_for_user`) | `agent` | none | Legacy sync helper. |
| `murder/verdict/escalations/model.py:39` | `escalations` INSERT + body file | `agent` | none | |
| `murder/verdict/escalations/model.py:48` | `escalations.body_path` UPDATE | `agent` | none | |
| `murder/verdict/escalations/model.py:60` | `escalations` resolve | `agent` | none | |
| **TICKET (`state.schedule_snapshot` ticket buckets + calendar fields)** | | | | |
| `murder/runtime/orchestration/orchestrator.py:203` | `tickets.status` → in_progress (`lifecycle.transition`) | `ticket` | `StatusChangeEvent` via `_emit_ticket_status` at `:204` | |
| `murder/runtime/orchestration/orchestrator.py:288` | ticket `.md` file create (prose) | `ticket` | none | `quick_create_ticket`; DB insert at `:308`. |
| `murder/runtime/orchestration/orchestrator.py:308` | `tickets` + deps + checklist INSERT | `ticket` | none | Race with TicketSync. |
| `murder/runtime/orchestration/orchestrator.py:326` | (emit only) ticket status change | `ticket` | `StatusChangeEvent` | `_emit_ticket_status` — F1 should add `StateSnapshotEvent{ticket}` beside this. |
| `murder/runtime/orchestration/orchestrator.py:1209` | `tickets.status` reopen cascade (`lifecycle.reopen`) | `ticket` | none | **Gap:** no `StatusChangeEvent` or snapshot emit. |
| `murder/runtime/orchestration/orchestrator.py:1217` | `tickets.status` → ready + clear error | `ticket` | `StatusChangeEvent` at `:1220` | |
| `murder/runtime/orchestration/orchestrator.py:1227` | `tickets.schedule_at` + `updated_at` | `ticket` | none | `set_schedule_at` RPC. |
| `murder/runtime/orchestration/orchestrator.py:1259` | `tickets.schedule_at` (in metadata update) | `ticket` | none | |
| `murder/runtime/orchestration/orchestrator.py:1263` | ticket title/harness/model/deps/checklist (`apply_ticket_carve_payload`) | `ticket` | none | `update_ticket_metadata` RPC. |
| `murder/runtime/orchestration/orchestrator.py:1286` | `tickets.status` force-set | `ticket` | `StatusChangeEvent` at `:1293` | |
| `murder/runtime/orchestration/orchestrator.py:1322` | carve payload + `tickets.status` → ready | `ticket` | `StatusChangeEvent` at `:1331` | via `apply_carve_ready_spec`. |
| `murder/work/tickets/carve.py:73` | deps/checklist/title/harness + transition ready | `ticket` | none at DAO; emit at orchestrator `:1331` | |
| `murder/work/tickets/lifecycle.py:56` | `tickets.status` (`update_ticket_status`) | `ticket` | none at DAO | All transition callers must emit. |
| `murder/work/tickets/lifecycle.py:62` | `tickets.last_error` NULL | `ticket` | none | Affects `last_update_label` in schedule snapshot. |
| `murder/work/tickets/lifecycle.py:70` | `tickets.last_error` SET | `ticket` | none | |
| `murder/runtime/orchestration/outcome.py:39` | transition → failed | `ticket` | `StatusChangeEvent` via `emit_status` at `:43` | |
| `murder/runtime/orchestration/outcome.py:48` | `tickets.status` → blocked | `ticket` | none | **Gap:** no status emit. |
| `murder/verdict/completion/coordinator.py:241` | transition → in_progress (completion edge) | `ticket` | none | Pre-done transition only. |
| `murder/verdict/completion/coordinator.py:247` | transition → done | `ticket` | `StatusChangeEvent` at `:249` | |
| `murder/verdict/completion/coordinator.py:273` | transition → failed | `ticket` | `StatusChangeEvent` at `:281` | |
| `murder/verdict/completion/coordinator.py:305` | `tickets.status` → blocked | `ticket` | none | **Gap:** no status emit. |
| `murder/app/service/recovery.py:98` | `tickets.status` → failed (stuck in_progress) | `ticket` | none | Startup reconcile. |
| `murder/app/cli/init_cmd.py:206` | `tickets` INSERT + `.md` write `:213` | `ticket` | none | CLI `murder init ticket`; service may be down. |
| `murder/app/cli/service_cmd.py:365` | `tickets.status` → planned (CLI retry) | `ticket` | none | CLI-only. |
| `murder/state/persistence/tickets.py:28` | `tickets` + deps + checklist INSERT | `ticket` | none | DAO `insert_ticket`. |
| `murder/state/persistence/tickets.py:76` | `tickets` title/harness/model UPDATE | `ticket` | none | `apply_ticket_carve_payload`. |
| `murder/state/persistence/tickets.py:84` | `ticket_deps` replace | `ticket` | none | Affects `pending_dep_ids` in schedule rows. |
| `murder/state/persistence/tickets.py:91` | `checklist` replace | `ticket` | none | Schedule snapshot does not list checklist; ticket_detail does. |
| `murder/state/persistence/tickets.py:144` | `tickets.status` UPDATE | `ticket` | none | |
| `murder/state/persistence/tickets.py:190` | `checklist` replace (`set_checklist`) | `ticket` | none | No production callers found (dead API?). |
| `murder/state/persistence/tickets.py:214` | `checklist` item done | `ticket` | none | No production callers found. |
| `murder/work/tickets/sync.py:105` | ticket reconcile txn (insert/update/deps/checklist/sync_state) | `ticket` | none | **Primary filesystem→DB path.** Hook at end of `reconcile_path` after COMMIT (`:120`). |
| `murder/work/tickets/sync.py:196` | `tickets` INSERT from parsed md | `ticket` | none | |
| `murder/work/tickets/sync.py:216` | `tickets` UPDATE from parsed md | `ticket` | none | |
| `murder/work/tickets/sync.py:233` | `ticket_deps` DELETE+INSERT | `ticket` | none | |
| `murder/work/tickets/sync.py:259` | `checklist` INSERT/UPDATE/DELETE | `ticket` | none | |
| `murder/work/tickets/sync.py:326` | `tickets.metadata_*` + `updated_at` (`_mark_sync_state`) | `ticket` | none | Drives `metadata_sync_state` / `last_update_label`. |
| `murder/work/tickets/sync.py:136` | materialize ticket `.md` from DB row | `ticket` | none | DB→filesystem; may race back through poll. |
| `murder/runtime/agents/crow_handler.py:254` | ticket `.md` working-notes append (`append_section`) | `ticket` | none | Prose only; affects `ticket_detail` + file hash on next sync. Emit on debounced sync, not per-note. |
| `murder/runtime/scheduler/worker.py:387` | enqueues `scheduler.kickoff_ready` command | `ticket` | none (command only) | Indirect: orchestrator kickoff mutates ticket/agent. |
| **QUEUE_ROW (usage gauges embedded in `state.schedule_snapshot`)** | | | | |
| `murder/llm/harnesses/usage_sampling.py:81` | `harness_usage_snapshots` INSERT | `queue_row` | none | Called from `UsageProbeWorker` on `state.harness_usage.sample`. |
| `murder/runtime/workers/usage_probe_worker.py:95` | triggers `sample_harness_usages` → inserts | `queue_row` | none | RPC `state.harness_usage.sample` / `scheduler.probe_usage`. |
| `murder/runtime/scheduler/worker.py:138` | DELETE old `harness_usage_snapshots` | `queue_row` | none | Prune only; low priority for UI. |
| `murder/runtime/scheduler/worker.py:420` | `scheduler_decision_cache` UPSERT | `queue_row` | `SchedulerDecisionEvent` at `:467` | Also affects schedule `scheduler_decisions` / `mode_rationale` → co-emit `ticket` or treat as `queue_row` per locked map. |
| `murder/runtime/scheduler/worker.py:244` | (detect only) usage reset | `queue_row` | `UsageResetEvent` at `:244` | Does not itself write DB; follows new snapshot insert. |
| `murder/runtime/scheduler/worker.py:500` | `scheduler_state.mode` UPDATE | `ticket` | `SchedulerModeEvent` at `:508` | Mode label in schedule snapshot header. |
| `murder/runtime/scheduler/worker.py:535` | `scheduler_params` UPSERT | `queue_row` | none | Changes crow_magic decisions on next tick. |
| `murder/runtime/scheduler/worker.py:122` | `scheduler_state` INSERT OR IGNORE | `ticket` | none | `on_start` seed only. |
| `murder/runtime/agents/crow_handler.py:303` | `agents.last_heartbeat_at` | `queue_row` | see agent row | Feeds `_burn_attribution` SQL in usage drill-in (not list gauges). |
| **PLAN (`state.plans_snapshot`)** | | | | |
| `murder/runtime/orchestration/orchestrator.py:934` | `plans` upsert + revision (`scaffold_plan`) | `plan` | none | Also writes markdown at `:944`. |
| `murder/runtime/orchestration/orchestrator.py:976` | plan rename via `PlanSync.rename_plan` | `plan` | none | |
| `murder/runtime/orchestration/orchestrator.py:992` | plan deprecate via `PlanSync.deprecate_plan` | `plan` | none | |
| `murder/work/plans/sync.py:107` | `plans` rename DB + markdown move | `plan` | none | |
| `murder/work/plans/sync.py:118` | `plans` UPDATE after rename | `plan` | none | |
| `murder/work/plans/sync.py:181` | `plans` supersede (`deprecate_plan`) | `plan` | none | |
| `murder/work/plans/sync.py:207` | `plans.sync_state` parse_error | `plan` | none | |
| `murder/work/plans/sync.py:222` | `plans` INSERT (new file import) | `plan` | none | `reconcile_file` |
| `murder/work/plans/sync.py:241` | `plans` UPDATE (file ingest) | `plan` | none | |
| `murder/work/plans/sync.py:264` | `plans` sync_state + body_hash | `plan` | none | `materialize_row` |
| `murder/work/plans/sync.py:265` | `plans.body_hash` UPDATE | `plan` | none | |
| `murder/state/persistence/plans.py:24` | `plan_revisions` INSERT + `revision_count` bump | `plan` | none | |
| `murder/state/persistence/plans.py:64` | `plans` INSERT (`upsert_plan`) | `plan` | none | |
| `murder/state/persistence/plans.py:86` | `plans` UPDATE (`upsert_plan`) | `plan` | none | |
| `murder/state/persistence/plans.py:107` | `plan_related_tickets` replace | `plan` | none | Not shown in list snapshot today. |
| `murder/state/persistence/plans.py:174` | `plans` INSERT (rename copy) | `plan` | none | |
| `murder/state/persistence/plans.py:203` | `plans` DELETE old name | `plan` | none | |
| `murder/state/persistence/plans.py:218` | `plans` supersede UPDATE | `plan` | none | |
| `murder/state/persistence/plans.py:249` | `plans.sync_state` UPDATE | `plan` | none | |
| `murder/state/persistence/conversation.py:81` | `agent_messages` replace (`merge_transcript`) | `plan` | none | Affects plans list **sort order** for `planner-{name}`. |
| `murder/state/persistence/conversation.py:667` | `agent_messages` append user | `plan` | `ConversationBlockEvent` at orchestrator `:655` or base `:205` | Planner chat re-sorts plans snapshot. |
| `murder/state/persistence/conversation.py:782` | `agent_messages` rebuild from blocks | `plan` | `ConversationBlockEvent` via producer/base | High-rate during agent poll. Coalesce plan emits. |
| `murder/state/persistence/agents.py:161` | `agent_messages` clear on conversation reset | `plan` | none | `conversation.clear` at conversation.py:58. |
| **NOTE (`state.notes_snapshot`)** | | | | |
| `murder/work/notes/sync.py:51` | `notes` INSERT (new file) | `note` | none | `NoteSync.reconcile_file` |
| `murder/work/notes/sync.py:61` | `notes` UPDATE (file edit) | `note` | none | |
| `murder/work/notes/sync.py:63` | `note_revisions` INSERT | `note` | none | |
| `murder/work/notes/sync.py:115` | `notetaker_context` UPDATE | — | none | **Not in notes snapshot** (separate table). |
| `murder/work/notes/__init__.py:86` | `notes` upsert (bootstrap import) | `note` | none | `ensure_note` |
| `murder/work/notes/__init__.py:90` | `notes` upsert empty bootstrap | `note` | none | |
| `murder/work/notes/__init__.py:113` | `notes` upsert (`write_note`) | `note` | none | |
| `murder/work/notes/__init__.py:137` | `notes` INSERT (`create_timestamped_note`) | `note` | none | Capture flow. |
| `murder/work/notes/__init__.py:181` | `notes` rename | `note` | none | |
| `murder/work/notes/__init__.py:214` | `notes` retire (`status=retired`) | `note` | none | |
| `murder/work/notes/__init__.py:386` | `notes_entries` INSERT + note create | `note` | none | `create_durable_capture` |
| `murder/work/notes/__init__.py:434` | `notes_entries.short_vers` UPDATE | — | none | Not in notes list snapshot. |
| `murder/work/notes/__init__.py:455` | note rename after capture metadata | `note` | none | `resolve_capture_note` |
| `murder/work/notes/__init__.py:492` | `write_note` on capture submit | `note` | none | `submit_capture` |
| `murder/work/notes/__init__.py:497` | `write_note` merge append | `note` | none | |
| `murder/runtime/orchestration/orchestrator.py:1185` | `submit_capture` RPC | `note` | none | Delegates to notes module. |
| `murder/runtime/orchestration/orchestrator.py:1196` | `ensure_note` RPC | `note` | none | |
| `murder/runtime/orchestration/orchestrator.py:1202` | `retire_note` RPC | `note` | none | |
| `murder/state/persistence/notes.py:44` | `notes` INSERT | `note` | none | DAO |
| `murder/state/persistence/notes.py:53` | `notes` UPDATE | `note` | none | |
| `murder/state/persistence/notes.py:71` | `notes` rename UPDATE | `note` | none | |
| `murder/state/persistence/notes.py:93` | `notes` retire UPDATE | `note` | none | |
| `murder/state/persistence/notes.py:112` | `note_revisions` INSERT | `note` | none | |
| **REPORT (`state.reports_snapshot`) — `Entity.REPORT` missing in Python** | | | | |
| *(none in `murder/` runtime)* | `.murder/agents/reports/*.md` created/edited/deleted externally | `report` | none | **No sync loop.** `DocumentAccess.open_report_in_editor` (`document_access.py:77`) opens editor but does not watch file. TUI/user/agent writes markdown out-of-band. F1 needs filesystem watcher or post-editor reconcile (like plans). |
| `murder/app/service/document_access.py:50` | `reports_dir` mkdir only | — | none | Does not mutate report list. |
| `murder/app/cli/init_cmd.py:67` | creates empty `reports/` dir | — | none | Init scaffolding only. |
| **Cross-check: existing bus publishers (pass 4)** | | | | |
| `murder/runtime/orchestration/orchestrator.py:326` | ticket status | `ticket` | `StatusChangeEvent` | Accounted above. |
| `murder/runtime/orchestration/orchestrator.py:655` | conversation block | — | `ConversationBlockEvent` | Not a snapshot entity; Ink uses separate subscription. |
| `murder/runtime/agents/crow_handler.py:92` | agent status | `agent` | `StatusChangeEvent` | |
| `murder/runtime/agents/crow_handler.py:241` | question | — | `QuestionEvent` | |
| `murder/runtime/agents/crow_handler.py:274` | heartbeat | — | `HeartbeatEvent` | |
| `murder/runtime/agents/crow_handler.py:291` | summary | — | `SummaryEvent` | |
| `murder/runtime/agents/crow_handler.py:347` | agent failed | `agent` | `StatusChangeEvent` | |
| `murder/runtime/agents/crow_handler.py:360` | error | — | `ErrorEvent` | |
| `murder/runtime/agents/crow.py:89` | agent running | `agent` | `StatusChangeEvent` | |
| `murder/runtime/agents/collaborator.py:104` | agent running | `agent` | `StatusChangeEvent` | |
| `murder/runtime/agents/planning_agent.py:85` | agent running | `agent` | `StatusChangeEvent` | |
| `murder/runtime/agents/planning_handler.py:86` | agent running | `agent` | `StatusChangeEvent` | |
| `murder/runtime/agents/base.py:205` | conversation block | — | `ConversationBlockEvent` | |
| `murder/runtime/scheduler/worker.py:244` | usage reset detect | `queue_row` | `UsageResetEvent` | |
| `murder/runtime/scheduler/worker.py:467` | scheduler decision | `queue_row` | `SchedulerDecisionEvent` | |
| `murder/runtime/scheduler/worker.py:508` | scheduler mode | `ticket` | `SchedulerModeEvent` | |
| `murder/runtime/workers/state_worker.py:42` | escalation snapshot | `ESCALATION` | `StateSnapshotEvent` | **Only key-only producer today.** |
| `murder/verdict/escalations/service.py:109` | escalation queue | `agent` | `EscalationEvent` | |
| `murder/verdict/completion/coordinator.py:249` | ticket done | `ticket` | `StatusChangeEvent` | |
| `murder/verdict/completion/coordinator.py:281` | ticket failed | `ticket` | `StatusChangeEvent` | |
| `murder/bus/transport_socket.py:413` | presence | — | `PresenceEvent` | Not a snapshot entity. |
| `murder/app/service/host.py:93` | command enqueue | — | `CommandEvent` | Internal worker queue. |

### Gaps to resolve

- **Reports have no backend writer or sync loop.** The snapshot reads `.murder/agents/reports/*.md` directly (`read_model.py:151-166`) but nothing in the service watches that directory; stale report list is guaranteed after out-of-band edits until restart/refetch luck.
- **`schedule_queue` table** exists in schema (`schema.py:321`) but has **no writers** (stub `scheduler.py`); not a snapshot input today.
- **Ink usage invalidation mismatch:** `inktui/src/store/usage/usageSlice.ts:47` sets `USAGE_INVALIDATING_ENTITY = 'agent'`, while the locked plan maps `queue_row → usage`. F1 must align (emit `queue_row` + fix Ink in F2).
- **High-rate paths:** `heartbeat_agent` (~poll interval), `project_parsed_doc` / conversation producer, and `SchedulerDecisionEvent` every crow_magic tick — need coalescing or emit-only-on-visible-change policy to avoid bus storms.
- **Ticket status gaps without any typed event:** `reopen_ticket` (`orchestrator.py:1209`), `block_ticket` (`outcome.py:48`, `coordinator.py:305`), startup `recovery.py:98`, CLI `service_cmd.py:365`.
- **`agent_messages` → plans sort order:** planner transcript updates reorder the plans list without any plan-row mutation; easy to miss if only watching `plans` table / `PlanSync`.
- **`ticket_detail` / prose:** checklist and `.md` prose mutations do not change schedule bucket membership but do change `state.ticket_detail`; no `Entity` for per-ticket detail today (demand-loaded). File edits eventually flow through `TicketSync` (`work/tickets/sync.py:105`).

### `queue_row` / usage source

**Confirmed:** there is no separate usage snapshot RPC and no `queue_row` table. `Entity.QUEUE_ROW` is the invalidation key for **usage gauges embedded in `state.schedule_snapshot`** (`schedule_snapshot.py:109-159` reads `harness_usage_snapshots`; decisions/mode from `scheduler_decision_cache` / `scheduler_state`). Usage drill-in (`state.usage_gauge_drill_in` / `build_usage_gauge_drill_in`) also reads `harness_usage_snapshots` plus live `agents`×`tickets` for burn rows. Emit `queue_row` when `harness_usage_snapshots` or scheduler decision cache changes usage-visible fields; emit `ticket` when scheduler **mode** changes.

### `Entity.REPORT` gap

**`Entity.REPORT` does not exist in `murder/bus/protocol.py`** (`Entity` enum lines 81-89 list `TICKET`, `AGENT`, `PLAN`, `NOTE`, `ESCALATION`, `QUEUE_ROW` only). Ink already declares `'report'` in `inktui/src/bus/protocol.ts:60` and `REPORTS_INVALIDATING_ENTITY = 'report'` in `reportsSlice.ts`. F1 must add `Entity.REPORT` to Python, bump `PROTOCOL_VERSION`, and emit `StateSnapshotEvent{report}` when the reports filesystem changes (requires a new watcher or sync loop).
