"""Recovery-safe evaluator for explicit trigger definitions."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from murder.facts.log import replay_facts
from murder.runtime.cron import iter_cron_fires
from murder.state.persistence.triggers import (
    StartWorkflow,
    fire_trigger,
    list_pending_manual_fires,
    list_triggers,
)
from murder.state.persistence.workflow_runs import create_workflow_run
from murder.work.triggers.runtime import (
    CronTrigger,
    FactTrigger,
    ManualTrigger,
    RepositoryTrigger,
    StartWorkflowTarget,
    TriggerDefinition,
)
from murder.work.workflows.runtime import (
    Correlation,
    PrincipalKind,
    PrincipalRef,
    VersionedState,
    WorkflowRunRecord,
    WorkflowStatus,
)

_CURSOR_PREFIX = "__cursor__:"


class TriggerOccurrenceProvider(Protocol):
    def cron(
        self, trigger: TriggerDefinition, spec: CronTrigger, cursor: str | None, now: datetime
    ) -> Sequence[str]: ...

    def facts(
        self, trigger: TriggerDefinition, spec: FactTrigger, cursor: str | None, now: datetime
    ) -> Sequence[str]: ...

    def repository(
        self,
        trigger: TriggerDefinition,
        spec: RepositoryTrigger,
        cursor: str | None,
        now: datetime,
    ) -> Sequence[str]: ...

    def manual(
        self, trigger: TriggerDefinition, spec: ManualTrigger, cursor: str | None, now: datetime
    ) -> Sequence[str]: ...


class DurableTriggerOccurrences:
    """Production occurrence source backed by durable state."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        repo_root: Path | None = None,
        repository_fingerprint: Callable[[Path], str] | None = None,
    ) -> None:
        self._connection = connection
        self._repo_root = None if repo_root is None else Path(repo_root)
        self._repository_fingerprint = repository_fingerprint or _repository_fingerprint

    def cron(
        self, trigger: TriggerDefinition, spec: CronTrigger, cursor: str | None, now: datetime
    ) -> Sequence[str]:
        del trigger
        if cursor is None:
            return (f"{_CURSOR_PREFIX}{now.astimezone(timezone.utc).isoformat()}",)
        after = _parse_aware(cursor)
        fires = iter_cron_fires(
            spec.expression,
            after=after,
            until=now,
            timezone=spec.timezone,
        )
        return tuple(fire.isoformat() for fire in fires)

    def facts(
        self, trigger: TriggerDefinition, spec: FactTrigger, cursor: str | None, now: datetime
    ) -> Sequence[str]:
        del trigger, now
        after_sequence = int(cursor) if cursor is not None else 0
        return tuple(
            (
                str(fact.sequence)
                if _payload_matches(fact.payload, spec.predicate)
                else f"{_CURSOR_PREFIX}{fact.sequence}"
            )
            for fact in replay_facts(
                self._connection,
                after_sequence=after_sequence,
                kind=spec.fact_kind,
                limit=100,
            )
        )

    def repository(
        self,
        trigger: TriggerDefinition,
        spec: RepositoryTrigger,
        cursor: str | None,
        now: datetime,
    ) -> Sequence[str]:
        del trigger
        root = self._repo_root
        if root is None:
            return ()
        expected_id = uuid5(NAMESPACE_URL, f"murder:repository:{root.resolve()}")
        if spec.repository_id != expected_id:
            return ()
        fingerprint = self._repository_fingerprint(root)
        if not fingerprint:
            return ()
        state = _parse_repository_cursor(cursor)
        if state is None:
            return (
                f"{_CURSOR_PREFIX}{_encode_repository_cursor(fingerprint, now, fired=False)}",
            )
        if fingerprint != state["fingerprint"]:
            return (
                f"{_CURSOR_PREFIX}{_encode_repository_cursor(fingerprint, now, fired=False)}",
            )
        if state["fired"]:
            return ()
        changed_at = _parse_aware(str(state["changed_at"]))
        if (now - changed_at).total_seconds() < spec.debounce_seconds:
            return ()
        return (f"repo:{fingerprint}@{state['changed_at']}",)

    def manual(
        self, trigger: TriggerDefinition, spec: ManualTrigger, cursor: str | None, now: datetime
    ) -> Sequence[str]:
        del spec, cursor, now
        return list_pending_manual_fires(self._connection, trigger.trigger_id)


class TriggerDispatcher:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        occurrences: TriggerOccurrenceProvider,
        start_workflow: StartWorkflow,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connection = connection
        self._occurrences = occurrences
        self._start_workflow = start_workflow
        self._clock = clock

    def tick(self, *, limit: int = 100) -> int:
        now = self._clock()
        fired = 0
        for trigger in list_triggers(self._connection):
            cursor = self._cursor(trigger.trigger_id)
            spec = trigger.spec
            if isinstance(spec, CronTrigger):
                keys = self._occurrences.cron(trigger, spec, cursor, now)
            elif isinstance(spec, FactTrigger):
                keys = self._occurrences.facts(trigger, spec, cursor, now)
            elif isinstance(spec, RepositoryTrigger):
                keys = self._occurrences.repository(trigger, spec, cursor, now)
            elif isinstance(spec, ManualTrigger):
                keys = self._occurrences.manual(trigger, spec, cursor, now)
            else:
                raise AssertionError("closed trigger spec")
            for key in keys:
                if key.startswith(_CURSOR_PREFIX):
                    self._set_cursor(trigger.trigger_id, key.removeprefix(_CURSOR_PREFIX), now)
                    continue
                fire_trigger(
                    self._connection,
                    trigger.trigger_id,
                    occurrence_key=key,
                    start_workflow=self._start_workflow,
                    now=now,
                )
                self._set_cursor(trigger.trigger_id, key, now)
                fired += 1
                if fired >= limit:
                    return fired
        return fired

    def _cursor(self, trigger_id: UUID) -> str | None:
        row = self._connection.execute(
            "SELECT cursor FROM trigger_cursors WHERE trigger_id = ?",
            (str(trigger_id),),
        ).fetchone()
        return None if row is None else str(row["cursor"])

    def _set_cursor(self, trigger_id: UUID, cursor: str, now: datetime) -> None:
        self._connection.execute(
            """
            INSERT INTO trigger_cursors(trigger_id, cursor, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trigger_id) DO UPDATE SET
                cursor = excluded.cursor, updated_at = excluded.updated_at
            """,
            (str(trigger_id), cursor, now.isoformat()),
        )


def build_default_trigger_dispatcher(
    connection: sqlite3.Connection,
    *,
    repo_root: Path | None = None,
) -> TriggerDispatcher:
    return TriggerDispatcher(
        connection,
        occurrences=DurableTriggerOccurrences(connection, repo_root=repo_root),
        start_workflow=_start_trigger_workflow,
    )


def _start_trigger_workflow(
    connection: sqlite3.Connection,
    target: StartWorkflowTarget,
    now: datetime,
) -> UUID:
    workflow_id = uuid4()
    create_workflow_run(
        connection,
        WorkflowRunRecord(
            workflow_id=workflow_id,
            definition_name=target.definition_name,
            definition_version=target.definition_version,
            status=WorkflowStatus.RUNNING,
            revision=0,
            state=VersionedState(
                schema_name=target.definition_name,
                schema_version=target.definition_version,
                value=target.inputs,
            ),
            created_at=now,
            updated_at=now,
            started_by=PrincipalRef(kind=PrincipalKind.SERVICE, id="fact-trigger"),
            correlation=Correlation(correlation_id=uuid4()),
        ),
    )
    return workflow_id


def _payload_matches(value: object, predicate: object) -> bool:
    if isinstance(predicate, dict):
        return isinstance(value, dict) and all(
            key in value and _payload_matches(value[key], expected)
            for key, expected in predicate.items()
        )
    if isinstance(predicate, list):
        return isinstance(value, list) and value == predicate
    return value == predicate


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("trigger cursor timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _repository_fingerprint(repo_root: Path) -> str:
    """Cheap change key: HEAD sha, falling back to ``.git`` mtime."""

    git_dir = repo_root / ".git"
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        head = result.stdout.strip()
        if head:
            return head
    if not git_dir.exists():
        return ""
    try:
        return f"mtime:{git_dir.stat().st_mtime_ns}"
    except OSError:
        return ""


def _encode_repository_cursor(fingerprint: str, changed_at: datetime, *, fired: bool) -> str:
    return json.dumps(
        {
            "fingerprint": fingerprint,
            "changed_at": changed_at.astimezone(timezone.utc).isoformat(),
            "fired": fired,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_repository_cursor(cursor: str | None) -> dict[str, object] | None:
    if cursor is None:
        return None
    if cursor.startswith("repo:") and "@" in cursor:
        # Occurrence-key cursor after a fire: fingerprint is already consumed.
        fingerprint, _, changed_at = cursor.removeprefix("repo:").partition("@")
        if not fingerprint or not changed_at:
            return None
        return {
            "fingerprint": fingerprint,
            "changed_at": changed_at,
            "fired": True,
        }
    try:
        payload = json.loads(cursor)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    fingerprint = payload.get("fingerprint")
    changed_at = payload.get("changed_at")
    fired = payload.get("fired")
    if not isinstance(fingerprint, str) or not isinstance(changed_at, str):
        return None
    return {
        "fingerprint": fingerprint,
        "changed_at": changed_at,
        "fired": bool(fired),
    }
