# Service Worker-Bus Refactor Plan Through Step 6

## Summary

Implement the service branch aggressively, using the branch split to replace weak single-process/runtime coupling instead of preserving it. Keep asking: "is the old code really the design we want?" especially around `Runtime`, `Orchestrator`, direct TUI writes, Sentinel reaching into live crow objects, and usage probing.

Canonical v1 topology is `4 + N` processes: Supervisor, TUI, Collaborator subprocess, UsageProbe subprocess, plus one CrowHandler+Crow subprocess per active ticket.

## Key Decisions

- Scheduler owns usage policy and routing; UsageProbeWorker owns expensive execution: tmux status probes, Cursor API fetch, normalization, metrics.
- TUI/web contract is protocol-first: clients use bus/RPC for state reads and commands. No direct DB writes from frontend clients.
- Read-only SQLite is allowed only as an internal optimization, not as the frontend contract.
- PaneMirrorWorker is presence-aware attended live visibility, not durable logging or pane-delta streaming.
- Crow subprocess boundary is per ticket and contains CrowHandler logic, harness adapter/crow, and tmux session.
- Future internal logic agents remain vision/context only, not implementation scope for this pass.

## Implementation Sequence

1. Done: add broker/DB foundation: `commands`, `worker_heartbeats`, `sentinel_state`, `events.schema_version`, command dual-write, command lifecycle helpers, and server-side `EventFilter` in the in-process broker path.
   - Expanded with durable replay/tail broker, RPC handler routing, and Unix-socket bus server.
2. Done: introduce `Worker`, `WorkerCtx`, worker specs, supervisor skeleton, heartbeat loop, local command dispatch, DB-backed command polling/claim/complete/fail, command reaper escalation, thread runner, and subprocess runner.
3. Done: lift PlanSync and NoteSync into worker wrappers.
4. Partially done: add service-side command workers for frontend boundary (`state.escalation.create`, usage probe command execution). TUI/web wiring and protocol/RPC read hydration are still pending.
5. Done as worker module: add CollaboratorWorker singleton subprocess shape. Runtime/TUI wiring is still pending.
6. Done as worker module: add UsageProbeWorker singleton subprocess shape. Scheduler policy and runtime wiring are still pending.
7. Pending: extract CrowHandler+Crow as one subprocess per ticket, with stable worker id derived from `ticket_id`.
8. Pending: replace direct Sentinel interventions against runtime/crow objects with explicit commands.

## Design Checkpoints

- Challenge old direct object access before porting it.
- Prefer protocol commands over "just call the orchestrator."
- Prefer one clear owner per lifecycle: Supervisor owns workers, Scheduler owns policy, UsageProbe owns probe execution, ProjectionWorker later owns markdown.
- Surface mid-implementation architecture ideas before locking them in, especially if they reduce coupling or remove accidental runtime inheritance.
- Defer max crow cap tuning until stress data exists.

## Tests

- Protocol round trips remain passing.
- DB migration/helper tests cover commands, heartbeats, sentinel state, schema version.
- Command dual-write verifies `commands` and `events` commit atomically.
- Server-side `EventFilter` filters command/snapshot/presence events before fanout.
- Worker contract tests cover declared `accepts`, `interests`, heartbeat behavior, and shutdown behavior.
- Supervisor command tests cover DB polling, completion, worker failure, stale retry, and exhausted-command escalation.
- Bus transport tests cover socket `hello/sub/pub/rpc/ack/err/wake` flow and replay semantics.
- Integration tests eventually cover supervisor plus one subprocess worker over socket and command reaper retry/fail.

Latest focused verification: `pytest -q tests/unit/test_supervisor_workers.py tests/unit/test_supervisor_commands.py tests/unit/test_db_commands.py tests/unit/test_bus_dual_write.py tests/unit/test_bus_broker.py tests/unit/test_process_runner.py tests/unit/test_collaborator_worker.py tests/unit/test_usage_probe_worker.py tests/unit/test_state_worker.py` passes with 24 passed.
