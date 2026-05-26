# Contributing to murder

Setup, conventions, and what tests are worth writing. Agent-only section at the bottom.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Python ≥3.10, hatchling backend. No `uv`/`poetry`/`pip-tools`.

## Tooling

`ruff check . && ruff format .`, `mypy --strict murder/`, `pytest`. No CI/pre-commit yet; run locally before PR.

## Layout

```
murder/
  agents/         crow, crow_handler, sentinel, collaborator
  bus/            broker + transports + client
  clients/        LLM API (anthropic, openai-compat, openrouter)
  harnesses/      claude_code, codex, cursor, pi, native adapters
  orchestration/  orchestrator, outcome, validator
  scheduler/      worker + usage curves
  service/        runtime, supervisor, recovery, bootstrap
  persistence/    sqlite schema, migrations, per-table modules
  tickets/ tui/ terminal/ storage/ plans/ notes/ escalations/
```

## Testing philosophy

Write fewer tests, deliberately. A test pins behavior; if the behavior is in flux or self-evident from the code, the test is debt. The Ousterhout principle applies to test suites too: prefer deep tests (high assertion-to-setup ratio, exercising real contracts) over shallow ones.

### Tiers

```
tests/
  unit/         hermetic, in-process, <1s each
  integration/  real orchestration, fake adapters/clients/harnesses
  smoke/        real TUI, real tmux, real or replayed agents
  support/      factories.py + simulators.py — extend, don't reinvent
  fixtures/     captured panes, golden outputs, (future) cassettes
```

`integration` = cross-module, in-process, seconds in CI. `smoke` = boots Textual or drives real subprocesses; slow, gated.

### What to test (priority order)

1. Contracts other code depends on: `bus` protocol, `harnesses/base`, `clients/base`, persistence schema. Hyrum's Law applies — once shipped, every observable becomes load-bearing, so pin the intended observables and only those.
2. State machines: crow/sentinel lifecycle, scheduler decisions, ticket waves. Invariants (no double-spawn, no orphans) aren't visible from reading.
3. Parsers/serializers: `tickets/parser`, `plans/parser`, `harnesses/parsing`. Cheap, high yield.
4. Regression tests for bugs you already debugged — worth ten speculative tests.

### What not to test

- Modules in flux (test once design settles).
- Pure delegation (the delegate's test covers it).
- Framework behavior (Textual rendering, sqlite storage, subprocess plumbing).

### Two heuristics

1. **When should this test break?** Name a concrete future change that should fail it. "No specific change" → testing nothing. "Any refactor" → testing implementation, per Feathers.
2. **Would deleting it be a loss?** No → don't write it.

Coverage % is not a goal; sharpness is.

### File structure

Cookbook first, edge cases second. Header comments are load-bearing.

```python
"""Tests for murder.bus.client.

COOKBOOK = canonical usage, copyable. EDGE CASES = real failure modes.
"""

# ============================================================
# === COOKBOOK ===============================================
# ============================================================
def test_publish_subscribe_roundtrip(): ...
def test_request_response(): ...

# ============================================================
# === EDGE CASES =============================================
# ============================================================
def test_subscribe_before_broker_ready_buffers_until_connect(): ...
def test_malformed_envelope_logs_and_drops(): ...
```

Cookbook blocks **mandatory** in: `bus/client.py`, `harnesses/base.py`, `orchestration/orchestrator.py`. Optional elsewhere; add one if the API isn't self-evident.

### Naming

Descriptive names beat docstrings. `pytest -v` should read as a behavioral spec six months from now.

### Hermeticity (unit tier, enforced in `conftest.py`)

No sockets (except bus transport tests with ephemeral ports), no subprocesses (use simulators), no I/O outside `tmp_path`, no wall-clock dependence (inject a clock; no `time.sleep`), no order dependence. Violations belong in `integration/` or `smoke/`.

### Builders, not fixture files

Use `tests/support/factories.py`. Shared JSON fixtures become twenty-test rewrites on schema change.

### Fakes, not mocks

`tests/support/simulators.py` over `MagicMock`. A fake that implements the protocol catches bugs; a mock that returns canned values catches none. Extend the simulators when you need new behavior.

### Live vs replay (planned)

Smoke tier will be cassette-replay by default with `--live` re-recording against real APIs.

```bash
pytest tests/smoke/           # replay
pytest tests/smoke/ --live    # re-record
```

Cassettes in `tests/fixtures/cassettes/`, not hand-edited.

### Markers (planned)

`@pytest.mark.slow` (>1s), `@pytest.mark.live` (needs `--live`), `@pytest.mark.quarantine` (skip in CI, fix or delete in 30 days).

---

## For agents (humans can stop reading)

Claude Code, Codex, Cursor: read this before writing any test.

### Default: don't write tests

Unless the user explicitly asked, don't. Scaffolding a directory, refactoring, "improving coverage" — none of these license new tests. If you finish a task and feel pulled to add tests, stop and ask. This is the most frequent failure mode in this repo and the most corrosive to trust; one prior incident produced ~100 tests that were deleted unread.

### When tests are appropriate

1. User asked.
2. Locking in a bug fix — one test, named after the bug.
3. New public API in a cookbook-mandatory module — add a cookbook entry, not a suite.

### Antipatterns (do not commit)

- **Shotgun coverage.** One test per function because functions exist. Many modules need zero. 100% coverage is corpobrain; a 40% sharp suite dominates a 100% noisy one.
- **Tautological tests.** `assert ClassName().print() prints`, `mock.foo.return_value = 3; assert obj.call_foo() == 3`, asserting that a constructor constructs. These test nothing.
- **Tautological mocking.** Mocking the unit under test, or so heavily that only the mocks are verified. Five mocks for one function = you tested the mocks.
- **Implementation-detail tests.** Private method calls, exact internal state sequences when only the final state is contracted, log message contents not part of the API. Behavior-preserving refactor breaks the test → test was wrong (Feathers).
- **Happy-path padding.** Five tests of the same path with different inputs = one test. Add real edge cases (malformed input, boundaries, concurrent access, partial failure) or consolidate.
- **Tests around buggy code.** Hard-to-test code is the bug. Don't contort the test; surface it as a code-quality observation. Sutherland's heuristic: if the test is hard, stop and fix the design.

### Pre-write checklist

1. **When should this break?** Name a specific change. "Any refactor" = locking implementation.
2. **Deletable without loss?** Yes = don't write it.
3. **Test setup elaborate?** Patching five modules, faking an event loop, monkeypatching `__import__` → the production code has the wrong shape. Stop. Report.

### Spec adherence

If you deviated from the user's spec, do not write tests that ratify the deviation. Surface it. Tests calcify whatever they cover, including mistakes.

### Cookbook sections

Respect the COOKBOOK/EDGE CASES split. Cookbook = canonical, documentation-grade. Don't pad it with edge cases.

### `tests/support/`

Need a fake harness/client/bus? Extend `simulators.py`. No inline `MagicMock` in test files. If the simulator is missing, add it there first.

### `tests/smoke/`

Not a junk drawer. If it doesn't boot the TUI or drive a real subprocess, it doesn't belong there. Codex: this means you.
