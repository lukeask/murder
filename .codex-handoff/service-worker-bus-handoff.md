# Service Worker-Bus Refactor Handoff

Date: 2026-05-14
Branch/worktree: `/home/user/Documents/code/murder/.agents/worktrees/service`

## Read First

Read these before editing:

1. `../../may13prefactor.md` — pre-refactor snapshot and mental model.
2. `../../may13servicerefactor.md` — service/backend branch handoff.
3. `.codex-handoff/service-worker-bus-plan.md` — current local execution plan and progress.
4. `../../worker_bus_refactor_v1.md` — long canonical background spec. Treat the May13 docs plus this handoff as the fresher local truth when they differ.
5. `murder/bus/protocol.py` — frozen protocol contract. Do not make breaking protocol changes casually.

Important conversation decisions:

- v1 topology is `4 + N`: Supervisor, TUI, Collaborator subprocess, UsageProbe subprocess, and one CrowHandler+Crow subprocess per active ticket.
- Scheduler owns policy; UsageProbeWorker owns expensive probe execution.
- TUI/web frontend contract is bus/RPC first. No direct frontend DB writes. Read-only SQLite is only an optimization, not the public client contract.
- Crow subprocess boundary is per ticket and contains CrowHandler logic, harness adapter/crow, and tmux session.
- Keep asking whether old `Runtime`/`Orchestrator` object coupling is really the design we want. Do not preserve it by reflex.

## What Landed

The current patch is intentionally foundation-heavy and tested.

Completed:

- Saved planning state in `.codex-handoff/service-worker-bus-plan.md`.
- Added DB schema and helpers in `murder/db.py`:
  - `commands`
  - `worker_heartbeats`
  - `sentinel_state`
  - `events.schema_version`
  - command enqueue/claim/complete/fail/reap helpers
  - command/event dual-write helper
- Updated `murder/bus/__init__.py` so `CommandEvent` writes `commands` and `events` in one transaction before fanout.
- Added `murder/bus/broker.py` with a transport-neutral `BusBroker` protocol and `InProcessBroker` adapter.
- Added durable service-side broker + transport:
  - `DurableBroker` in `murder/bus/broker.py` with DB replay/tail subscriptions and RPC handler routing.
  - `SocketBusServer` in `murder/bus/transport_socket.py` implementing `hello/sub/pub/rpc/ack/err/wake` over Unix sockets.
  - Socket path default follows protocol constants (`$XDG_RUNTIME_DIR/murder/bus.sock` with `/tmp/murder-$UID/bus.sock` fallback).
- Added worker/supervisor skeletons:
  - `murder/supervisor.py`
  - `murder/workers/base.py`
  - `murder/workers/thread_runner.py`
  - `murder/workers/process_runner.py`
  - `murder/workers/sync_workers.py`
- Added worker modules:
  - `PlanSyncWorker`
  - `NoteSyncWorker`
  - `CollaboratorWorker`
  - `UsageProbeWorker`
  - `StateCommandWorker`
- Wired supervisor DB-backed command execution:
  - command polling/claim loop using `claim_next_command`
  - claimed row conversion back into protocol `CommandEvent`
  - worker completion/failure writes through command lifecycle helpers
  - stale command reaper loop using `reap_stale_commands`
  - `EscalationEvent` publish for exhausted commands
- Wired TUI command boundary through bus-backed commands for key workflows:
  - usage refresh (`state.harness_usage.sample`, `scheduler.probe_usage`)
  - collaborator chat send (`collaborator.chat_send`)
  - collaborator transcript refresh (`collaborator.transcript.refresh`)
  - notetaker chat send (`notetaker.chat.send`)
  - kickoff (`scheduler.kickoff_ready`)
  - UI escalation creation (`state.escalation.create`)
- Added startup wiring in `murder/cli.py` to run supervisor workers during TUI sessions:
  - `StateCommandWorker`
  - `UsageProbeWorker`
  - `CollaboratorWorker`
  - `OrchestratorCommandWorker`
- Added tests for DB commands, command dual-write, broker adapter, supervisor/worker behavior, process runner, collaborator worker, usage probe worker, and state command worker.
- Added supervisor command lifecycle tests for claim/complete/fail/retry/escalate.
- Added orchestrator worker tests for kickoff and notetaker chat command handling.
- Removed remaining TUI live-agent reads for collaborator/crow session lookups and collaborator transcript refresh; TUI now uses command + DB session lookup for those paths.
- Added socket/broker tests for replay+tail subscriptions, wire-level hello/sub/rpc flow, and durable RPC routing.

Verification already run:

```bash
pytest -q
```

Result:

```text
274 passed, 21 skipped
```

Also ran targeted `ruff check` on new worker/broker/test files; it passed.

## Design Notes From Implementation

Two old-code smells were fixed during the first pass:

- `UsageProbeWorker` originally wanted to take `Runtime`. That was wrong for a subprocess boundary. It now takes injected sampler/kinds-provider dependencies, with `from_runtime(...)` only as a migration shim while the existing sampling helper still depends on `Runtime`.
- The first worker skeleton had a separate local `WorkerCommand` path. It now also accepts real `CommandEvent` dispatch so the implementation points toward the frozen bus protocol instead of a parallel command abstraction.

More corrections:

- `reap_stale_commands(...)` now returns `{"retried": [...], "failed": [...]}` and applies retry/exhaustion logic. The supervisor should emit escalations for failed command ids later.
- Supervisor command dispatch preserves the queue row id separately from `CommandEvent.id`, because existing DB helper tests use text ids while the protocol event id is a UUID.
- Supervisor lease expiry uses `math.ceil(...)` instead of integer truncation so sub-second test/config TTLs do not immediately self-expire.
- Supervisor heartbeat loop now persists `worker_heartbeats` rows via DB helper.
- Added DB helpers for `worker_heartbeats` and `sentinel_state` read/write paths.

## Next Work

Do these next, in this order.

1. Start replacing runtime-owned background loops.
   - Integrate `PlanSyncWorker` and `NoteSyncWorker` into the supervisor path.
   - Do not keep adding startup responsibilities to `Runtime` unless there is a clear migration reason.
   - Emit `StateSnapshotEvent` after plan/note mutations where practical.

2. Continue frontend boundary cleanup.
   - Escalation creation, usage refresh, chat send, and kickoff now flow through command events.
   - Remaining direct runtime object reads for collaborator transcript/pane hints should move behind bus/RPC.
   - Add command-backed reads for richer TUI hydration to reduce runtime coupling further.

3. Wire `CollaboratorWorker`.
   - Existing worker module is dependency-injected and tested.
   - Next step is supervisor-managed singleton subprocess startup.
   - Be careful: current `orchestrator.ensure_collaborator()` reaches through live runtime objects and tmux checks. Prefer replacing that ownership with supervisor worker lifecycle rather than wrapping it forever.

4. Wire `UsageProbeWorker`.
   - Keep it a singleton subprocess.
   - Scheduler decides when to probe; UsageProbe only executes.
   - Add timing metrics around probes.
   - Probe cadence must stay configurable.

5. Start CrowHandler+Crow process extraction.
   - One subprocess per active ticket.
   - Stable worker id derived from `ticket_id`.
   - Process owns CrowHandler logic, harness adapter/crow, and tmux session.
   - This is the main crash isolation payoff.

6. Sentinel/Notetaker follow after process boundaries are proven.
   - Sentinel direct runtime/crow interventions must become explicit commands.
   - Sentinel state belongs in `sentinel_state`.
   - Notetaker message history is still a persistence/replay risk; do not overbuild unless it blocks extraction.

## Files To Inspect Before Next Edits

- `murder/supervisor.py`
- `murder/workers/base.py`
- `murder/workers/collaborator_worker.py`
- `murder/workers/usage_probe_worker.py`
- `murder/workers/state_worker.py`
- `murder/db.py`
- `murder/bus/__init__.py`
- `murder/runtime.py`
- `murder/orchestrator.py`
- `murder/tui/app.py`
- `murder/harnesses/usage_sampling.py`

## Tests To Run

Minimum focused loop:

```bash
pytest -q \
  tests/unit/test_db_commands.py \
  tests/unit/test_bus_dual_write.py \
  tests/unit/test_bus_broker.py \
  tests/unit/test_supervisor_workers.py \
  tests/unit/test_supervisor_commands.py \
  tests/unit/test_process_runner.py \
  tests/unit/test_collaborator_worker.py \
  tests/unit/test_usage_probe_worker.py \
  tests/unit/test_state_worker.py
```

Before handing back:

```bash
pytest -q
```

Run `ruff check` on touched files. Do not run formatters that rewrite unrelated files.

## Current Git State Expectations

This worktree has modified tracked files and many new untracked files. Do not reset or discard anything.

Expected tracked modifications:

- `murder/bus/__init__.py`
- `murder/db.py`
- `tests/unit/test_db_schema.py`

Expected new files include:

- `.codex-handoff/service-worker-bus-plan.md`
- `.codex-handoff/service-worker-bus-handoff.md`
- `murder/bus/broker.py`
- `murder/supervisor.py`
- `murder/workers/*`
- new `tests/unit/test_*worker*.py`, `test_bus_broker.py`, `test_bus_dual_write.py`, `test_db_commands.py`, `test_process_runner.py`

## Architectural Bias

When choosing between a small compatibility wrapper and a cleaner ownership boundary, prefer the cleaner boundary on this branch. The branch exists so service and TUI can move in parallel and so old runtime coupling can be replaced decisively.
- Added service bootstrap wiring in `murder/cli.py`:
  - starts `SocketBusServer` for bus clients
  - registers RPC handlers: `health.ping`, `command.submit`, `command.status`
  - uses `DurableBroker` as the supervisor bus context
