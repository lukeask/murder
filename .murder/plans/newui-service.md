---
created_at: '2026-06-07T00:00:00'
name: newui-service
parent: newui
related_plans: [newui, newui-inktui, plan-tui-data-render-split]
status: draft
phase: null
---

# New UI — Service / Backend

The **renderer-agnostic backend** half of the `newui` rewrite (see [[newui]] for the full
concept, the thought-vomit, and the Textual→Ink architecture decision). This plan is built
**first**, on branch `ink-rewrite`. None of it depends on the frontend framework — it sits
below the JSON-RPC-over-socket bus and survives the Textual→Ink switch unchanged. The Ink
frontend ([[newui-inktui]]) is authored separately, after this lands.

Status: spec locked (2026-06-07). Ready to start (B1+B2 first — zero-risk foundations).

## Goal

Land the data-model, sync, model-discovery, attribution, and bus-surface changes the new
UI needs, behind clean swappable seams, so that:
- every artifact is **one `.md`** (frontmatter + body), runtime state is **DB-only**;
- `wave` and `skills` are gone; per-ticket `worktree` exists; deps expose pending ids;
- model lists are **discovered at startup**, not hardcoded;
- malformed artifacts get the **owning agent** re-prompted (swappable attribution seam);
- the TUI surface is a **pure RPC consumer** (bus-coupling violations closed), so the Ink
  client — and the future web/phone clients — talk only JSON-RPC to the service.

## Guiding principles

- **One on-disk standard for artifacts.** Minimize what agents must author; never make them
  author structured data they're bad at. MCP-free: agents use normal file/shell tools only.
- **Runtime state lives in the DB, never in agent-authored files.**
- **Swappable seams (hygiene).** `attribute_edit(path)`, `get_available_models(harness)`,
  `reconcile_ticket_md()` — single interfaces so impls swap wholesale later.
- **The bus is the boundary.** Everything a frontend needs is an RPC method; no frontend
  touches the DB or the filesystem directly.

## Approach (backend decisions, locked 2026-06-07)

### Artifact metadata standard

- **One `.md` per artifact.** Kill the ticket `.yaml` sidecar; tickets become structurally
  like plans (frontmatter + body).
- **Short frontmatter = agent-authored fields only:** `title, deps, harness, model,
  worktree` (default none), `parent` (plans; default none).
- **`skills` is cut** — never a real concept, an agent hallucinated it. Drop the
  `ticket_skills` table, the edge-sync, and all callers (not just from frontmatter).
- **Runtime/DB-owned fields never appear in the file:** `status, schedule_at, attempts`,
  sync hashes. Deletes the file-owned-vs-DB-owned status conflict machinery (a current
  source of parse errors/conflicts).
- **Forgiving parser:** tolerate aliases, ignore unknown keys, never hard-fail ingest.
- **Checklist lives in the body** under `# Checklist` as `[ ]`/`[x]`, parsed body→DB by the
  sync worker. Crow toggles `[x]` with a normal file edit; example instructs editing
  incrementally as each item completes, not at the end.
- **DEPRECATE / DELETE (both hallucinated, not load-bearing):** emit-YAML-block-in-chat
  carve flow, and the `>>> CHECK` pane marker.
- **Copyable templates** `example_ticket.md` / `example_plan.md` WITH frontmatter + a
  `# Checklist` section + the incremental-check instruction. A default ticket is copyable,
  re-created by the service if removed, and not shown in the TUI.

### Tickets

- **Cut wave entirely.** Only live use is `scheduler_policy._ticket_sort_key` → replace
  with `(schedule_at is None, schedule_at, id)`. Drop the column via migration; parser
  treats `wave` as optional/ignored.
- **Per-ticket worktree field** (default NONE), **per-ticket only** — the global
  `config.runtime.use_worktrees` flag is removed. `spawn_crow` provisions a worktree iff
  the ticket sets one. Reuse the rogue plumbing (`spawn_rogue` already takes
  `worktree_path`; tmux `cd`s into the worktree root via `-c`).
  - **Checklist-in-worktree resolution (Q2):** the ticket `.md` is canonical in the **main
    repo** at `.murder/tickets/{id}.md`. A crow in `.murder/worktrees/crow/{seg}/` has its
    own checkout, so it must edit the *canonical main-repo path*, not the worktree copy —
    the sync worker watches that one location. The reference-by-path kickoff points the crow
    at that absolute canonical path. One source of truth, one watched file.
    - **Codex sandbox:** codex is the only harness needing a grant — launch it with
      `--add-dir <abs main-repo>/.murder/tickets` so it can write the canonical ticket while
      sandboxed in its worktree cwd. CC and Cursor already permit edits there; this just
      makes codex uniform. (`--add-dir` is directory-scoped; no per-file grant needed.)
- **Remove "no model override."** Every ticket carries explicit harness+model; the
  `default_crow` pool becomes the fallback. (The empty UI option is removed in [[newui-inktui]].)
- **Deps display data:** show only the ids of deps that are NOT done. Widen
  `ScheduleTicketRow` to carry the pending-dep id list (today only `deps_ok: bool`). The
  cell rendering itself is the Ink frontend's job.
- **Free-form schedule duration parsing** (`1d4h3m`, `1h1m`, `34m`, `1h`) — provide a small
  shared `parse_duration()` util in the backend; the input widget lives in [[newui-inktui]].

### HARNESSES_AND_MODELS.md + dynamic models

- Generate at murder startup from **dynamic discovery** — `discover_harness_models()`
  already exists (incl. Cursor's paginated scroll) but is NEVER called at startup. Wire it
  to run at startup and cache; the hardcoded `available_startup_models` classvars demote to
  fallback only.
- Regenerate on settings change (hook after `SettingsService.save_project/save_global` +
  `reconfigure_collaborator`).
- Lists enabled harnesses + models + effort levels. Effort is sparse/per-harness:
  CC (low/med/high/xhigh/max), Codex (low/med/high/xhigh), Cursor (slow/fast); others none.
- Planner prompt reads it iff writing a ticket.
- Effort plumbing: the adapter support is intact; ensure the `crow.spawn_rogue` RPC payload
  carries `effort` end-to-end (the scrapped Textual wizard dropped it; the Ink wizard will
  collect it).

### Agent file-edit attribution + metadata-error reprompt

Goal: when an agent writes a malformed artifact under `.murder/`, message *that agent* to
fix it. Delivery is free — `agent.message {agent_id, message}` already exists. The hard part
is **attribution**.

- **Implement now — convention-based attribution:** owner known by naming
  (`planner-{plan}` owns its plan + carved tickets; `crow-{id}` owns its ticket). On a parse
  error, route the fix-message to that owning agent.
- **Design for the seam — keep it swappable.** Single interface
  `attribute_edit(path) -> agent_id | None` so it can be replaced wholesale by
  **pane-derived attribution** later (parse tmux panes → general edit→agent log; reusable
  infra). Pane-derived shelved for now; the seam is a hygiene requirement regardless.

### Bus-coupling cleanup (prereq for the Ink frontend)

The TUI must be a **pure RPC consumer** before the Ink client replaces it. Audit complete
(2026-06-07): three write-violations, four read/coupling-violations, DB layer clean.

| # | File | Line | What it does | Fix |
|---|------|------|--------------|-----|
| V1 | `app/tui/app.py` | ~1796 | `_quick_create_ticket` writes `.murder/tickets/<id>.md` directly (docstring admits the bypass); sibling `_quick_kick_ticket` already uses `ticket.quick_kick` RPC | New `ticket.quick_create` RPC |
| V2 | `app/tui/note_capture.py` | ~182 | `_paste_image()` writes clipboard PNG to `.murder/images/<ts>-<hex>.png` | Extend `notetaker.capture.submit` to accept image bytes, or add `image.upload` RPC → ref |
| V3 | `app/tui/crows_view.py` | ~239 | `_save_favorites` writes `.murder/tui_prefs.json` (atomic tmp+replace) | `tui.save_favorites` RPC, **or** keep as TUI-local prefs (allowlist candidate) |
| V4 | `app/tui/app.py` | ~101–108 | `_next_ticket_id()` globs `.murder/tickets/*.md` to find max id | `ticket.next_id` RPC |
| V5 | `app/tui/app.py` | ~114 | `_is_ticket_handle()` checks `.murder/tickets/<handle>.yaml` on disk | `ticket.exists` RPC (or fold into V4) |
| V6 | `app/tui/client.py` | ~104–110 | imports `choose_editor` from backend + `subprocess.run()` to launch editor on a plan file | `editor.open` RPC (or source editor binary via RPC) |
| V7 | `app/tui/app.py` | ~26, 29–31 | imports `is_rogue_agent_id`, `list_murder_worktrees_sync` (DB-touching), `format_session_name` from backend | expose via RPC or demote to pure stateless utils |

Out-of-scope (not `.murder/` domain data): `chat_input.py` writes clipboard PNGs to system
`/tmp`; `perf_log.py` appends opt-in perf JSONL to `.murder/logs/tui_perf.log` (diagnostic).

**Allowlist (shared types, no state access — fine in an Ink client too, no action):**
`murder.state.storage.paths` constants; `TicketStatus` enum; `ModelDiscoveryResult` /
`WorktreeEntry` DTOs. Pure data-shape imports.

## Bus contract (shared — keep [[newui-service]] and [[newui-inktui]] in sync)

> **This block is duplicated verbatim in both plans.** It is the one interface the two halves
> build against, so service and Ink can be developed **in parallel** without reading each
> other's internals. Change it in both copies, or not at all. The service **implements** this
> surface; the Ink store **consumes** it.

**Transport.** One Unix-socket JSON-RPC connection, multiplexed. The service is the sole
authority over DB + filesystem; the view never touches disk/DB — every read and write is a
method call or a subscription on this socket. (A later WS bridge re-exposes this *same*
surface; designing to it now is what makes the bridge cheap — see inktui "Deferred".)

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
- `crow.spawn_rogue {…, effort}` — spawn a rogue/crow; payload **carries `effort`** end-to-end (B10).
- `agent.message {agent_id, message}` — deliver a message to an agent.
- `notetaker.capture.submit {…}` — submit a captured note.

*New — added by C14/B13 (the V-list closure; Ink F0 depends on these existing).
IMPLEMENTED 2026-06-08, sha 74331dc — shapes below are final, mirror into [[newui-inktui]]:*
- `ticket.quick_create {title}` → `{handled, ticket_id, title}` (replaces the direct
  `.md` write, V1). Routed through `command.submit` (target_worker `orchestrator`,
  kind `ticket.quick_create`) since it mutates DB+fs.
- `ticket.next_id {}` → `{ok, ticket_id}` next free ticket id (V4). Direct RPC.
- `ticket.exists {handle}` → `{ok, exists}` bool (V5). Direct RPC. Checks DB row OR
  on-disk `.md` (the old `.yaml` sidecar check is dead post-C2/C3).
- `editor.binary {preferred?}` → `{ok, editor}` — **CHOSEN over `editor.open`**: the
  service is a tty-less daemon, so it returns the resolved editor *command* and the
  client launches the subprocess locally (the client owns the user's terminal) (V6).
- **`image.upload {bytes, ext?}` → `{ok, path}` (V2 — CHOSEN over extending
  `notetaker.capture.submit`).** `bytes` is **base64** over JSON-RPC; `ext` defaults
  to `png`. Images are pasted inline mid-draft and referenced as `![image](path)`
  before submit (and there can be several), so a standalone upload fits the flow.
  Stored under `.murder/images/note-img-<ts>-<hex>.<ext>`.
- `tui.save_favorites {favorites: [id,…]}` → `{ok, favorites}` — persist favorite
  agent ids (V3). **Paired with `tui.load_favorites {}` → `{ok, favorites: [id,…]}`**
  (added so the Ink store can *read* them; the load path also had to leave `.murder/`).
- `worktree.list {}` → `{ok, entries: [{path, branch, is_main},…]}` — list `.murder`
  worktrees (V7; the spawn wizard's worktree options build off these). Direct RPC.

### Events / subscriptions (service → view)

- `state.snapshot` — **key-only** change events; the store invalidates exactly the named slice.
- **tmux frame stream** — a subscription opened **only in raw mode** (`ctrl+y`): streams ANSI
  frames for the focused pane, closed when parsed view returns (no standing cost).

### Payload / DTO shapes crossing the boundary (changing in this rewrite)

- **`ScheduleTicketRow.pending_dep_ids: tuple[str,…]`** replaces `deps_ok: bool` — the row
  carries the ids of *non-done* deps; the Ink deps cell renders them (B5).
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
  the precondition (inktui F0 depends on B13).
- **Key-only events** mean the service can add new change-keys without breaking the view, and
  the view ignores keys it doesn't subscribe to — the two evolve independently between contract
  revisions.

## Work breakdown

**Layer 0 — foundations (no deps)**

| ID | What | Owns (writes) | Deps |
|----|------|---------------|------|
| B1 | DB migrations: drop `wave` (table-recreate; register AFTER draft/archived status migrations; guard on `sqlite_master`); `ADD COLUMN worktree TEXT`; drop `ticket_skills` table | `state/persistence/{schema,migrations}.py` | — |
| B2 | Unified ticket file module: `parse_ticket` / `render_ticket_frontmatter` modeled on `work/plans/parser.py`; **forgiving** (never raises, unknown→extras, surfaces `parse_error`); frontmatter = `title,deps,harness,model,worktree`; body `# Checklist` (level-1) parse, only `[ ]`/`[x]` lines after header | `work/tickets/parser.py`, new `work/tickets/render.py` | — |

**Layer 1 — sync + wave removal (dep: B1, B2)**

| ID | What | Owns (writes) | Deps |
|----|------|---------------|------|
| B3 | Unified ticket sync worker: replaces `TicketSync` + `TicketMetadataSync`; deletes `sidecar.py`/`sidecar_sync.py`; frontmatter→DB, body→DB checklist (**preserve `done_at`**), seed missing `.md`; synchronous `reconcile_ticket_md()`; drop skills edge-sync | new `work/tickets/sync.py`; del `sidecar*.py`; `app/service/filesystem_sync.py` | B1, B2 |
| B4 | Cut `wave` everywhere: records, tickets DAO, `scheduler_policy._ticket_sort_key` → `(schedule_at is None, schedule_at, id)`, scheduler `worker.py`, `client_api` DTOs, `schedule_snapshot`, `read_model`, `orchestrator.evaluate_wave_completion` (del), `waves.py` (del), `service_cmd` lint, `init_cmd` | ~14 files (see explorer report) | B1 |

**Layer 2 — behaviour (dep: Layer 1)**

| ID | What | Owns (writes) | Deps |
|----|------|---------------|------|
| B5 | Deps-display data: `ScheduleTicketRow.deps_ok:bool` → `pending_dep_ids:tuple[str,...]` (SQL `GROUP_CONCAT` of non-done deps across the 3 selects); scheduler's own `NOT EXISTS` *filter* unchanged | `schedule_snapshot.py`, `client_api.py` | B4 |
| B6 | Per-ticket worktree spawn: `spawn_crow` reads `ticket.worktree`, provisions iff set; remove `config.runtime.use_worktrees` | `runtime/orchestration/orchestrator.py`, `config.py` | B1 |
| B7 | Deprecate carve-YAML + `>>>CHECK`: del `carve.py` YAML path, orchestrator yaml branch, `init_cmd` ingest cmd, `crow_handler.detect_checks` call, `base.CHECK_RE`; keep ASK/DONE/NOTE/ANSWER markers | `work/tickets/carve.py`, `runtime/agents/crow_handler.py`, `llm/harnesses/base.py`, orchestrator, `init_cmd` | B3 |
| Bd | `parse_duration()` shared util (`1d4h3m`/`34m`/…) for schedule input | new util in `work/` or `verdict/` | — |

**Layer 3 — models + spawn + attribution (parallel; light dep on B1/B2)**

| ID | What | Owns (writes) | Deps |
|----|------|---------------|------|
| B8 | Startup model discovery + cache: fire `discover_harness_models()` per enabled harness after `start_supervisor_workers` (graceful timeout); `get_available_models(harness)` accessor (cache → classvar fallback); demote the 3 classvars; route `settings`/`spawn` reads through accessor | `app/.../host.py`, new model-cache module, `llm/harnesses/*` | — |
| B9 | `HARNESSES_AND_MODELS.md` generator: pure `render_harnesses_doc(enabled, models)`; write at startup (post-discovery) + after `SettingsService.save_project/save_global`; planner prompt reads iff writing a ticket | new generator, `settings_service.py`, `prompts/planner.md` | B8 |
| B10 | Effort through the spawn bus command: ensure `crow.spawn_rogue` RPC payload carries `effort` end-to-end → `spawn_rogue(effort=…)` | `runtime/workers/orchestrator_worker.py`, orchestrator spawn payload | — |
| B11 | Attribution seam + metadata-error reprompt: `attribute_edit(path) -> agent_id \| None` (convention impl); on parse error route fix via `agent.message`; swappable to pane-derived later | new attribution module; parse-error hook | B2 |
| B12 | Templates: `example_ticket.md` / `example_plan.md` (frontmatter + `# Checklist` + incremental-check instruction); service restores default ticket if removed (hidden from TUI) | `.murder` templates, service restore hook | B2 |

**Layer 4 — bus-surface (closes the V-list above; unblocks the Ink frontend)**

| ID | What | Owns (writes) | Deps |
|----|------|---------------|------|
| B13 | New RPC methods: `ticket.quick_create` (V1), `ticket.next_id` (V4), `ticket.exists` (V5), `editor.open` (V6); image-upload path for V2; `tui.save_favorites` or prefs decision (V3); demote V7 backend imports to stateless utils/RPC | `app/service/host.py` + handlers; `app/tui/*` callers | B3 |

Recommended start: **B1 + B2** (zero-risk foundations) → B3 → B4 → rest. B8–B12 run in
parallel. B13 can start once B3 stabilizes the ticket RPCs.

## Single-agent execution chunks (sequential)

The table above is organized by dependency *layer* for parallel work. The breakdown below
re-casts it as a **linear queue**: each chunk is scoped for **one Opus Claude Code agent,
working alone, start-to-finish in one session** (implement + tests + self-verify + commit),
then hand off to the next. Ordering respects deps and minimizes mid-flight file conflicts
since chunks run one at a time. Each chunk is **independently committable and leaves the tree
green** — the next agent starts from a passing state.

**Per-chunk contract (every chunk):**
- Re-read this plan's relevant section before starting; the plan is the source of truth.
- Implement only the listed scope. If you discover the scope is wrong, stop and note it in the
  plan rather than expanding silently.
- Add/adjust tests for the behaviour you changed; run the suite; leave it green.
- Commit on `ink-rewrite` with a message naming the chunk id (e.g. `feat(newui): C3 …`).
- Tick this chunk's box below and append a one-line "done: <sha>, <notes>" before handing off.

---

- [x] **C1 — DB migrations (was B1).** Drop `wave` (table-recreate; register the migration
  *after* the draft/archived status migrations; guard on `sqlite_master`), `ADD COLUMN worktree
  TEXT`, drop the `ticket_skills` table. *Files:* `state/persistence/{schema,migrations}.py`.
  *Done when:* fresh-DB init and migrate-from-existing both pass; no `wave`/`ticket_skills` in
  schema; `worktree` column present. *No deps.*
  done: 6654ec4, fresh and existing ticket schema migration tests pass.

- [x] **C2 — Unified ticket file module (was B2).** `parse_ticket` / `render_ticket_frontmatter`
  modeled on `work/plans/parser.py`. **Forgiving**: never raises, unknown keys → extras,
  surfaces `parse_error` instead of throwing. Frontmatter = `title, deps, harness, model,
  worktree`. Body `# Checklist` (level-1) parse, only `[ ]`/`[x]` lines after the header.
  *Files:* `work/tickets/parser.py`, new `work/tickets/render.py`. *Done when:* round-trip
  (parse→render→parse) is stable; malformed inputs return a `parse_error` rather than raising;
  unit tests cover aliases/unknown-keys/missing-fields/checklist. *No deps.*
  done: 58c5de7, focused ticket parser tests and touched-file ruff pass; broader wave-era
  schedule snapshot test still needs C4 cleanup.

- [x] **C3 — Unified ticket sync worker (was B3).** Replace `TicketSync` + `TicketMetadataSync`;
  delete `sidecar.py` / `sidecar_sync.py`. Frontmatter→DB, body→DB checklist (**preserve
  `done_at`** on existing items), seed missing `.md`; synchronous `reconcile_ticket_md()`; drop
  the skills edge-sync. *Files:* new `work/tickets/sync.py`; delete `sidecar*.py`;
  `app/service/filesystem_sync.py`. *Done when:* a `.md` edit reflects in the DB; checklist
  toggles persist with `done_at` preserved; no sidecar references remain. *Deps: C1, C2.*
  done: f8fdf17, unified ticket markdown sync replaces sidecar loops; focused sync/parser/schema tests pass.

- [x] **C4 — Cut `wave` everywhere (was B4).** Records, tickets DAO,
  `scheduler_policy._ticket_sort_key` → `(schedule_at is None, schedule_at, id)`, scheduler
  `worker.py`, `client_api` DTOs, `schedule_snapshot`, `read_model`,
  `orchestrator.evaluate_wave_completion` (delete), `waves.py` (delete), `service_cmd` lint,
  `init_cmd`. *(~14 files — grep `wave` to confirm full set before starting.)* *Done when:*
  `grep -ri wave` over the backend returns only unrelated hits; scheduler ordering tests pass.
  *Deps: C1.*
  done: 47abe4a, backend wave references removed; scheduler ordering and affected DTO tests pass.

- [x] **C5 — Deps-display data (was B5).** `ScheduleTicketRow.deps_ok: bool` →
  `pending_dep_ids: tuple[str, …]` (SQL `GROUP_CONCAT` of non-done deps across the 3 selects);
  the scheduler's own `NOT EXISTS` *filter* stays unchanged. *Files:* `schedule_snapshot.py`,
  `client_api.py`. *Done when:* row DTO carries pending ids; scheduler gating behaviour
  unchanged. *Deps: C4.*
  done: 7dc1726, schedule row DTO now carries pending dependency ids; focused snapshot and scheduler tests pass.

- [x] **C6 — Per-ticket worktree spawn (was B6).** `spawn_crow` reads `ticket.worktree` and
  provisions a worktree iff set (reuse the rogue `worktree_path` plumbing; codex gets
  `--add-dir <main-repo>/.murder/tickets`). Remove `config.runtime.use_worktrees`. *Files:*
  `runtime/orchestration/orchestrator.py`, `config.py`. *Done when:* a ticket with `worktree`
  set spawns into a worktree editing the *canonical main-repo* ticket path; global flag gone.
  *Deps: C1.*
  done: dd9ac29, per-ticket worktree spawn uses ticket.worktree; focused worktree and harness tests pass.

- [ ] **C7 — Deprecate carve-YAML + `>>>CHECK` (was B7).** Delete the `carve.py` YAML path, the
  orchestrator yaml branch, the `init_cmd` ingest cmd, the `crow_handler.detect_checks` call,
  and `base.CHECK_RE`. **Keep** ASK / DONE / NOTE / ANSWER markers. *Files:*
  `work/tickets/carve.py`, `runtime/agents/crow_handler.py`, `llm/harnesses/base.py`,
  orchestrator, `init_cmd`. *Done when:* no YAML-carve or CHECK path remains; surviving markers
  still work. *Deps: C3.*

- [x] **C8 — `parse_duration()` util (was Bd).** Shared duration parser (`1d4h3m`, `1h1m`,
  `34m`, `1h`) for schedule input. *Files:* new util in `work/` or `verdict/`. *Done when:*
  unit tests cover each documented format + malformed input. *No deps.* *(Small — could be
  folded into C5 if an agent finishes early, but kept separate for a clean queue.)*
  done: 490f378, new pure `work/duration.py` returns timedelta and raises ValueError on
  malformed input (anchored fullmatch rejects empty/bare-number/unknown-unit/out-of-order);
  no prior duration-string parser existed; 18 focused tests + ruff green.

- [x] **C9 — Startup model discovery + cache (was B8).** Fire `discover_harness_models()` per
  enabled harness after `start_supervisor_workers` (graceful timeout). Add
  `get_available_models(harness)` accessor (cache → classvar fallback); demote the 3
  `available_startup_models` classvars to fallback; route settings/spawn reads through the
  accessor. *Files:* `app/.../host.py`, new model-cache module, `llm/harnesses/*`. *Done when:*
  startup populates the cache; accessor falls back gracefully when discovery times out. *No
  hard deps (light on C1/C2).*
  done: fe07a11, new `llm/harnesses/model_cache.py` (in-process cache + `get_available_models`
  accessor cache→classvar fallback + `populate_model_cache` firing per discovery-capable harness
  concurrently behind `asyncio.wait_for` per-harness timeouts, all failures swallowed); host fires
  it as a tracked `_model_discovery_task` after `start_supervisor_workers` and cancels it in
  `stop()` like the other poll tasks; rerouted the 3 settings/spawn reads (settings_screen,
  spawn_wizard, roster) through the accessor (left orchestrator degraded-ok check + cursor's own
  fallback untouched). Note: classvars demoted by *role* not renamed (kept for cursor-internal +
  orchestrator getattr use); discovery-capable set is actually all 6 harnesses (the "3 classvars"
  in the brief = the non-empty ones), handled uniformly. 10 focused cache tests + ruff green; the
  pre-existing test_transcript[cc] failure is unrelated (claude_code grammar WIP).
  C10/C14 note: `spawn_wizard._HARNESS_MODELS` still hardcodes claude_code/codex model lists
  ahead of the accessor (only empty-list harnesses fall through to `get_available_models`); when
  cross-process discovery delivery lands it will need to defer to discovered models for those two.

- [x] **C10 — `HARNESSES_AND_MODELS.md` generator (was B9).** Pure
  `render_harnesses_doc(enabled, models)`; write at startup (post-discovery) and after
  `SettingsService.save_project/save_global` (+ `reconfigure_collaborator`); planner prompt
  reads it iff writing a ticket. *Files:* new generator, `settings_service.py`,
  `prompts/planner.md`. *Done when:* doc regenerates on settings change and lists
  harnesses/models/effort levels. *Deps: C9.*
  done: 9389874, new `llm/harnesses/harnesses_doc.py` — pure `render_harnesses_doc(enabled,
  models)` (effort derived from adapter `supported_efforts` classvar, not a param; empty-model
  harness listed as "(no models discovered)") + `write_harnesses_doc(repo_root)` I/O helper that
  reads models via C9's `get_available_models` (cache→classvar fallback) and lists only the
  project's *enabled* crow harnesses (from `Config.default_crow.harnesses` pool — same set the
  settings screen edits, so a disabled harness with a non-empty classvar fallback like codex is
  omitted and the planner can't assign it). Startup chains write after `populate_model_cache`
  (host `_discover_then_write_models_doc`, not a racing 2nd task). Hooked into `save_global`,
  `save_project`, and `orchestrator.reconfigure_collaborator`. `paths.harnesses_and_models_md` →
  `.murder/HARNESSES_AND_MODELS.md`. Planner prompt gets one line to read the doc when carving.
  13 doc tests + 3 settings-hook tests + ruff green; pre-existing test_transcript[cc] failure
  unrelated (claude_code grammar WIP, fails on baseline). C11 note: spawn `effort` still TODO.

- [x] **C11 — Effort through the spawn bus (was B10).** Ensure the `crow.spawn_rogue` RPC
  payload carries `effort` end-to-end → `spawn_rogue(effort=…)`. *Files:*
  `runtime/workers/orchestrator_worker.py`, orchestrator spawn payload. *Done when:* an effort
  value supplied at the RPC boundary reaches the adapter. *No deps.*
  done: fa36727, backend path was ALREADY complete — no code change needed. Traced full path:
  JSON-RPC ingress `host._command_submit` passes `payload` opaquely into `CommandEvent.payload`
  (no per-method field whitelist) → `orchestrator_worker.on_command` forwards `dict(payload)`
  verbatim to the spawn callable → `orchestrator.spawn_rogue_command` unpacks+validates `effort`
  → `spawn_rogue(effort=…)` → `startup_effort` into `get_harness`/`CrowAgent`/`HarnessStartSpec`
  → adapter. Effort was wired by the worktree-support work (435026f, 2026-05-30), predating this
  chunk; the C10 "still TODO" note was stale. The ONLY drop is the Textual `app._do_spawn_rogue`
  + `SpawnWizard.Confirmed` (no effort field) — intentionally deferred to the Ink wizard per the
  plan, NOT in this chunk's Files list, so left untouched (no gold-plating a doomed frontend).
  Deliverable = regression tests `tests/unit/test_spawn_effort_bus.py` (3 tests: worker forwards
  full payload incl. effort; spawn_rogue_command forwards effort to spawn_rogue; non-string
  effort rejected at the boundary). All green + ruff clean; existing test_orchestrator_worker
  still passes.

- [x] **C12 — Attribution seam + metadata-error reprompt (was B11).** `attribute_edit(path) ->
  agent_id | None` (convention impl: `planner-{plan}` owns its plan+tickets, `crow-{id}` owns
  its ticket). On a parse error, route a fix-message via `agent.message`. Keep the interface
  single/swappable (pane-derived later). *Files:* new attribution module; parse-error hook.
  *Done when:* a malformed `.murder/` artifact messages its owning agent. *Deps: C2.*
  done: 21b47ba, new pure `work/attribution.py` (`attribute_edit(path, repo_root) -> agent_id |
  None`; ticket→`crow-{stem}`, plan→`planner-{stem}`; string/path-based, no DB/live lookup;
  deprecated_plans/ + non-.md guarded out). Planner-owns-carved-tickets case deliberately deferred
  to the seam swap (ticket .md has no parent-plan field → not path-derivable). Notifier wired as
  optional `parse_error_notifier` on both TicketSync (returns parse_error from sync `reconcile_path`,
  awaits notify in async `reconcile_file`) and PlanSync (notifies in its async parse-error branch).
  Spam guard: notify ONLY from the debounced edit-watch `reconcile_file`, suppressed in the
  startup/shutdown bulk `reconcile_all` (verified markdown_loop.poll_once only calls reconcile_file
  once per observed mtime/size change). FilesystemSyncSupervisor.set_parse_error_notifier composes
  attribute_edit + message-build + send; host wires it to orchestrator.send_agent_message after the
  orchestrator is built (sync loops start earlier in Runtime.start, so attached late). 14 focused
  tests (pure attribution + ticket/plan notify-once + reconcile_all-no-notify + supervisor
  end-to-end composition + unattributable-skip) + ruff green; full unit suite green except the
  pre-existing `test_transcript[cc]` baseline failure (claude_code grammar WIP, unrelated).

- [x] **C13 — Templates (was B12).** `example_ticket.md` / `example_plan.md` (frontmatter +
  `# Checklist` + incremental-check instruction); service restores the default ticket if
  removed (hidden from TUI). *Files:* `.murder` templates, service restore hook. *Done when:*
  deleting the default ticket triggers re-creation; templates are copyable and not surfaced in
  the TUI. *Deps: C2.*
  done: 85827a1, canonical templates in tracked `murder/resources/templates/example_{ticket,plan}.md`
  (copied to `.murder/` top level at runtime, gitignored). New pure `work/examples.py:seed_examples()`
  (idempotent: restores only missing files, preserves user edits) wired into
  `FilesystemSyncSupervisor.reconcile_all` (startup/shutdown → delete then next reconcile re-creates)
  and `init_cmd._scaffold_project`. Hiding is symmetric+automatic: examples sit at `.murder/` top
  level, NOT in `tickets/`/`plans/`, which both sync loops glob only their own subdir of — so neither
  ingests them and neither surfaces in the TUI (the no-digit `_TICKET_ID_RE` guard would also reject
  `example_ticket`, but top-level placement is the real mechanism and covers the plan too, whose
  parser is non-forgiving). 7 focused tests (both parsers parse the templates clean, restore-after-
  delete, idempotency/edit-preservation, supervisor end-to-end) + ruff green.

- [x] **C14 — New RPC methods / V-list closure (was B13).** `ticket.quick_create` (V1),
  `ticket.next_id` (V4), `ticket.exists` (V5), `editor.open` (V6); pick + implement the
  image-upload path for V2 (**record the choice in the Bus contract block above**);
  `tui.save_favorites` or a prefs decision (V3); demote V7 backend imports to stateless
  utils/RPC. *Files:* `app/service/host.py` + handlers; `app/tui/*` callers. *Done when:* every
  V-row in the bus-coupling table is closed and the TUI no longer touches `.murder/`/DB
  directly. *Deps: C3.* **This is the last service chunk — it unblocks the Ink frontend
  ([[newui-inktui]] F0).**
  done: 74331dc, all 7 V-rows closed. V1 ticket.quick_create (orchestrator command +
  worker + bootstrap; quick_kick now delegates to quick_create_ticket — no double insert).
  V4 ticket.next_id / V5 ticket.exists (direct RPC off orchestrator next_ticket_id()/
  ticket_exists(); exists checks DB-row OR .md, dropped the dead .yaml check). V6 →
  `editor.binary` NOT editor.open (service is tty-less; client launches subprocess with the
  resolved cmd). V2 → `image.upload {bytes(base64), ext}` → {path} (chose standalone RPC over
  extending capture.submit — inline multi-paste flow; recorded in Bus contract block above).
  V3 → `tui.save_favorites` + added `tui.load_favorites` (both sides had to leave .murder/;
  crows roster now uses async IO callables not a prefs_path). V7 → is_rogue_agent_id demoted
  to pure `runtime/orchestration/agent_ids.py` (re-exported from orchestrator for back-compat);
  `worktree.list` RPC returns WorktreeEntry DTOs; format_session_name was already pure.
  Final grep: app/tui/ builds no .murder/ paths and imports no DB/persistence/backend helpers
  (only TicketStatus enum + WorktreeEntry DTO remain — allowlisted); perf_log .murder/logs
  writes left as out-of-scope diagnostics per the plan. 26 focused tests green (3 new files +
  updated worker/effort/note tests); full unit suite green except the pre-existing
  test_transcript[cc] (claude_code grammar WIP, modified+failing on baseline, untouched here).
  CONTRACT DRIFT: the Bus contract block above was synced (V2 + editor.binary choice +
  tui.load_favorites + worktree.list); [[newui-inktui]]'s verbatim copy needs the SAME four
  edits — the Ink author builds against that block, so it must not rot.

---

**Queue order rationale.** C1→C2 are the zero-risk foundations everything builds on. C3 (sync)
then C4 (wave removal) settle the data layer before behaviour. C5–C8 are small behaviour
chunks. C9–C13 are largely independent and could be reordered, but the linear queue keeps one
agent's blast radius isolated. C14 runs **last** because it depends on C3 having stabilized the
ticket RPCs, and its completion is the precondition for starting the Ink frontend.
