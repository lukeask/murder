# Test Guidelines

This directory is for tests that explain and protect the behavior of `murder`.
The goal is not coverage theater. A useful test should either document a public
contract, preserve an important invariant, or catch a realistic failure mode.

## Test Types

### Unit Tests

Unit tests are the default test type in this directory. They should be small,
deterministic, fast, and focused on one module or public API boundary at a time.

Good unit tests read like executable documentation. Someone unfamiliar with the
implementation should be able to open a test file and learn how the API is meant
to be used, what it promises, and how it behaves at the edges.

### Integration Tests

Integration tests are a separate beast and are intentionally out of scope for
most unit-test work. Tests that need a live tmux server, real harness process,
real API client, filesystem project fixture, or long-running async supervisor
behavior should live under an explicit integration lane and use the
`@pytest.mark.integration` marker.

Do not smuggle integration behavior into unit tests through sleep-heavy async
flows, real subprocesses, or broad end-to-end setup. If a test needs live
infrastructure, say so clearly and keep it out of the main unit-test bulk.

## Unit Test File Shape

Each test file should be organized around the contract it documents. Prefer one
test file per module or closely related API surface.

Use this structure unless there is a strong local reason not to:

```python
"""Tests for murder.some_module.

These tests document the public contract for SomeThing and the edge cases that
must remain stable.
"""

import pytest

from murder.some_module import SomeThing


# Examples / API contracts
# ------------------------


def test_some_thing_accepts_the_minimal_valid_input():
    result = SomeThing.from_config({"name": "demo"})

    assert result.name == "demo"
    assert result.enabled is True


def test_some_thing_serializes_to_the_documented_shape():
    thing = SomeThing(name="demo", enabled=False)

    assert thing.to_dict() == {"name": "demo", "enabled": False}


# Edge cases / regressions
# ------------------------


def test_some_thing_rejects_missing_name():
    with pytest.raises(ValueError, match="name"):
        SomeThing.from_config({"enabled": True})
```

The examples section should usually be about one third of the file. It is the
cookbook: normal usage, documented shapes, expected defaults, and public API
contracts.

The edge-cases section should usually be about two thirds of the file. It should
cover invalid input, boundary values, ordering, idempotency, persistence
round-trips, async cancellation/error paths, and regressions for bugs that would
be easy to reintroduce.

## What Makes a Test Worth Keeping

A good unit test has a reason to exist. Before adding one, identify the contract
or failure mode it protects.

Prefer:

- Behavior that callers can observe.
- Stable public contracts over private implementation details.
- Small fixtures built from real project types.
- Focused fakes at process, network, clock, or filesystem boundaries.
- Clear assertions on values, emitted events, persisted records, or raised
  errors.
- Test names that read as claims about behavior.

Avoid:

- Tests that only restate the implementation line by line.
- Mocks of every collaborator when a small real object would be clearer.
- Assertions on incidental call order unless order is the contract.
- Snapshot dumps that are too broad to review.
- Tests that pass even if the feature is deleted or the assertion is trivial.
- Sleeping, polling, or depending on wall-clock timing in unit tests.

## Agent Checklist

When an agent writes tests in this directory, it should do this first:

1. Read the target module and the nearest public caller.
2. Identify the public contract being protected.
3. Draft example tests before edge-case tests.
4. Add edge cases that would catch plausible mistakes, not imaginary ones.
5. Use existing fixtures and helper patterns when present.
6. Run the narrow test file first, then the relevant broader test command.
7. Remove tests that do not assert meaningful behavior.

If the contract is unclear, write the test around the behavior that already has
the strongest evidence in code or docs, and leave a short note in the PR or task
summary about the ambiguity.

## Running Tests

Run the focused file while developing:

```bash
pytest tests/path/to/test_file.py
```

Run the whole unit suite before handing off:

```bash
pytest
```

Integration and smoke tests may require local services, tmux, harness binaries,
or environment variables. They should be marked explicitly and should not be
required for the default unit-test pass.
