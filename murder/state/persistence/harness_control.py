"""Durable storage for verified harness interaction.

This module is the persistence boundary for terminal frames, broad
harness-specific evidence, normalized observations, semantic events, and
operation/action/effect history.  It intentionally does not share tables with
the conversation transcript or generic command queue: neither can express
evidence provenance or unsafe-action recovery semantics without losing facts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from murder.llm.harness_control.model.actions import (
    EffectEmission,
)
from murder.llm.harness_control.model.evidence import (
    EvidenceDiagnostics,
    EvidenceEnvelope,
    EvidenceRef,
    ScreenRegionRef,
    TerminalFrame,
)
from murder.llm.harness_control.model.observations import (
    AuthoritativeFacts,
    ChoiceState,
    ComposerActionability,
    ComposerState,
    GenerationPhase,
    GenerationState,
    HarnessInfoState,
    Knowledge,
    ModalKind,
    ModalState,
    ModelConfigurationState,
    ModelState,
    ObservationDelta,
    ObservationHealth,
    ObservationRevision,
    ObservationSnapshot,
    Observed,
    PermissionRequestState,
    QuestionState,
    SessionSettingsState,
    SurfaceKind,
    SurfaceState,
    ToolActivityState,
    ToolInteraction,
    TranscriptTailState,
    TurnRef,
    UsageState,
    UsageWindow,
)
from murder.llm.harness_control.model.operations import (
    ActionRecord,
    DecisionRecord,
    OperationEnvelope,
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="microseconds")


def _session_key(session_id: str | None) -> str:
    """Use a stable non-NULL key so SQLite revision uniqueness is real."""
    return session_id or ""


def _type_name(value: object) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _json_value(value: object) -> object:  # noqa: PLR0911 - explicit type encoding is safer
    """Convert architecture values without silently stringifying semantics.

    Type markers make stored records inspectable and reprocessable even when a
    payload includes enums, dataclasses, timedeltas, or explicit knowledge
    wrappers.  Harness evidence payload dictionaries remain dictionaries; no
    lowest-common-denominator projection occurs here.
    """
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, timedelta):
        return {"$type": "timedelta", "seconds": value.total_seconds()}
    if isinstance(value, Enum):
        return {"$type": _type_name(value), "name": value.name}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": _type_name(value),
            "fields": {item.name: _json_value(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return {"$type": "tuple", "items": [_json_value(item) for item in value]}
    if isinstance(value, frozenset):
        return {
            "$type": "frozenset",
            "items": [_json_value(item) for item in sorted(value, key=repr)],
        }
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"cannot persist harness-control value of type {_type_name(value)}")


def _json_dump(value: object) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_load(raw: str) -> object:
    return json.loads(raw)


_OBSERVATION_DATACLASSES: tuple[type[object], ...] = (
    ScreenRegionRef,
    EvidenceRef,
    ObservationRevision,
    Observed,
    SurfaceState,
    ComposerState,
    GenerationState,
    TurnRef,
    TranscriptTailState,
    ModalState,
    ChoiceState,
    PermissionRequestState,
    QuestionState,
    ModelState,
    ModelConfigurationState,
    SessionSettingsState,
    HarnessInfoState,
    UsageWindow,
    UsageState,
    ToolInteraction,
    ToolActivityState,
    ObservationHealth,
    AuthoritativeFacts,
    ObservationSnapshot,
)
_OBSERVATION_ENUMS: tuple[type[Enum], ...] = (
    Knowledge,
    SurfaceKind,
    ComposerActionability,
    GenerationPhase,
    ModalKind,
)
_OBSERVATION_TYPES: dict[str, type[object]] = {
    f"{value_type.__module__}.{value_type.__qualname__}": value_type
    for value_type in (*_OBSERVATION_DATACLASSES, *_OBSERVATION_ENUMS)
}


def _decode_observation_value(  # noqa: PLR0911, PLR0912 - persisted markers are explicit
    value: object, *, path: str = "snapshot"
) -> object:
    """Decode only allowlisted observation types; persisted JSON cannot import code."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_decode_observation_value(item, path=f"{path}[]") for item in value]
    if not isinstance(value, dict):
        raise ValueError(f"{path}: unsupported persisted JSON value")
    marker = value.get("$type")
    if marker is None:
        return {
            str(key): _decode_observation_value(item, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if not isinstance(marker, str):
        raise ValueError(f"{path}: persisted type marker must be a string")
    if marker == "datetime":
        if set(value) != {"$type", "value"} or not isinstance(value["value"], str):
            raise ValueError(f"{path}: invalid persisted datetime")
        return datetime.fromisoformat(value["value"])
    if marker == "timedelta":
        seconds = value.get("seconds")
        if set(value) != {"$type", "seconds"} or isinstance(seconds, bool) or not isinstance(
            seconds, (int, float)
        ):
            raise ValueError(f"{path}: invalid persisted timedelta")
        return timedelta(seconds=seconds)
    if marker in {"tuple", "frozenset"}:
        items = value.get("items")
        if set(value) != {"$type", "items"} or not isinstance(items, list):
            raise ValueError(f"{path}: invalid persisted {marker}")
        decoded = tuple(
            _decode_observation_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(items)
        )
        return decoded if marker == "tuple" else frozenset(decoded)
    value_type = _OBSERVATION_TYPES.get(marker)
    if value_type is None:
        raise ValueError(f"{path}: unsupported persisted observation type {marker!r}")
    if issubclass(value_type, Enum):
        name = value.get("name")
        if set(value) != {"$type", "name"} or not isinstance(name, str):
            raise ValueError(f"{path}: invalid persisted enum")
        try:
            return value_type[name]  # type: ignore[index]
        except KeyError as exc:
            raise ValueError(f"{path}: unknown {value_type.__name__} member {name!r}") from exc
    encoded_fields = value.get("fields")
    if set(value) != {"$type", "fields"} or not isinstance(encoded_fields, dict):
        raise ValueError(f"{path}: invalid persisted dataclass")
    expected = {item.name for item in fields(value_type)}
    if set(encoded_fields) != expected:
        raise ValueError(f"{path}: schema mismatch for {value_type.__name__}")
    decoded_fields = {
        name: _decode_observation_value(item, path=f"{path}.{name}")
        for name, item in encoded_fields.items()
    }
    try:
        return value_type(**decoded_fields)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid {value_type.__name__}: {exc}") from exc


def _regions_json(regions: tuple[ScreenRegionRef, ...]) -> str:
    return _json_dump(regions)


def _regions_from_json(raw: str) -> tuple[ScreenRegionRef, ...]:
    value = _json_load(raw)
    # `_json_value` marks tuples; retain a small backwards-compatible reader for
    # future hand-written SQL fixtures that provide a plain JSON array.
    rows = value.get("items", []) if isinstance(value, dict) else value
    result: list[ScreenRegionRef] = []
    for row in rows if isinstance(rows, list) else []:
        fields_value = row.get("fields") if isinstance(row, dict) else None
        data = fields_value if isinstance(fields_value, dict) else row
        if not isinstance(data, dict):
            continue
        result.append(
            ScreenRegionRef(
                label=str(data.get("label", "unknown")),
                start_line=_plain_int(data.get("start_line")),
                end_line=_plain_int(data.get("end_line")),
                start_column=_plain_int(data.get("start_column")),
                end_column=_plain_int(data.get("end_column")),
            )
        )
    return tuple(result)


def _plain_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def persist_frame(
    conn: sqlite3.Connection,
    frame: TerminalFrame,
    *,
    session_id: str | None = None,
) -> None:
    """Insert an immutable raw frame; duplicate ids must describe the same frame."""
    existing = conn.execute(
        "SELECT harness_id, captured_at, raw_text FROM harness_control_frames WHERE frame_id = ?",
        (str(frame.frame_id),),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["harness_id"]) != str(frame.harness_id)
            or str(existing["captured_at"]) != frame.captured_at.isoformat()
            or str(existing["raw_text"]) != frame.raw_text
        ):
            raise ValueError(f"frame id {frame.frame_id!r} already identifies different content")
        return
    conn.execute(
        """
        INSERT INTO harness_control_frames(
            frame_id, harness_id, session_id, captured_at, width, height, raw_text,
            ansi_preserved, pane_epoch, capture_sequence, stored_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(frame.frame_id),
            str(frame.harness_id),
            session_id,
            frame.captured_at.isoformat(),
            frame.width,
            frame.height,
            frame.raw_text,
            int(frame.ansi_preserved),
            frame.pane_epoch,
            frame.capture_sequence,
            _now(),
        ),
    )


def get_frame(conn: sqlite3.Connection, frame_id: str) -> TerminalFrame | None:
    row = conn.execute(
        "SELECT * FROM harness_control_frames WHERE frame_id = ?", (frame_id,)
    ).fetchone()
    if row is None:
        return None
    return TerminalFrame(
        frame_id=str(row["frame_id"]),
        harness_id=str(row["harness_id"]),
        captured_at=datetime.fromisoformat(str(row["captured_at"])),
        width=int(row["width"]),
        height=int(row["height"]),
        raw_text=str(row["raw_text"]),
        ansi_preserved=bool(row["ansi_preserved"]),
        pane_epoch=int(row["pane_epoch"]),
        capture_sequence=int(row["capture_sequence"]),
    )


def persist_evidence(conn: sqlite3.Connection, evidence: EvidenceEnvelope) -> None:
    """Persist broad parser output after its supporting raw frame exists."""
    frame = conn.execute(
        "SELECT harness_id FROM harness_control_frames WHERE frame_id = ?",
        (str(evidence.frame_id),),
    ).fetchone()
    if frame is None:
        raise ValueError(f"evidence {evidence.evidence_id!r} references an unknown frame")
    if str(frame["harness_id"]) != str(evidence.harness_id):
        raise ValueError("evidence harness does not match its frame harness")
    existing = conn.execute(
        "SELECT frame_id, parser_version, evidence_type, payload_json "
        "FROM harness_control_evidence WHERE evidence_id = ?",
        (str(evidence.evidence_id),),
    ).fetchone()
    payload_json = _json_dump(evidence.payload)
    if existing is not None:
        if (
            str(existing["frame_id"]) != str(evidence.frame_id)
            or str(existing["parser_version"]) != evidence.parser_version
            or str(existing["evidence_type"]) != evidence.evidence_type
            or str(existing["payload_json"]) != payload_json
        ):
            raise ValueError(
                f"evidence id {evidence.evidence_id!r} already identifies different evidence"
            )
        return
    diagnostics = {
        "parser_name": evidence.diagnostics.parser_name,
        "messages": evidence.diagnostics.messages,
        "unrecognized_regions": evidence.diagnostics.unrecognized_regions,
        "contradictory_fields": evidence.diagnostics.contradictory_fields,
    }
    conn.execute(
        """
        INSERT INTO harness_control_evidence(
            evidence_id, frame_id, harness_id, parser_version, evidence_type, captured_at,
            payload_json, source_regions_json, diagnostics_json, stored_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(evidence.evidence_id),
            str(evidence.frame_id),
            str(evidence.harness_id),
            evidence.parser_version,
            evidence.evidence_type,
            evidence.captured_at.isoformat(),
            payload_json,
            _regions_json(evidence.source_regions),
            _json_dump(diagnostics),
            _now(),
        ),
    )


def get_evidence(conn: sqlite3.Connection, evidence_id: str) -> EvidenceEnvelope | None:
    row = conn.execute(
        "SELECT * FROM harness_control_evidence WHERE evidence_id = ?", (evidence_id,)
    ).fetchone()
    return _evidence_from_row(row) if row is not None else None


def list_evidence(
    conn: sqlite3.Connection,
    *,
    harness_id: str | None = None,
    frame_id: str | None = None,
    evidence_type: str | None = None,
) -> list[EvidenceEnvelope]:
    clauses: list[str] = []
    params: list[str] = []
    for column, value in (
        ("harness_id", harness_id),
        ("frame_id", frame_id),
        ("evidence_type", evidence_type),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM harness_control_evidence{where} ORDER BY captured_at, evidence_id", params
    ).fetchall()
    return [_evidence_from_row(row) for row in rows]


def _evidence_from_row(row: sqlite3.Row) -> EvidenceEnvelope:
    diagnostic_value = _json_load(str(row["diagnostics_json"]))
    if isinstance(diagnostic_value, dict) and "fields" in diagnostic_value:
        diagnostic_value = diagnostic_value["fields"]
    diagnostic_value = diagnostic_value if isinstance(diagnostic_value, dict) else {}
    regions_value = diagnostic_value.get("unrecognized_regions", {"$type": "tuple", "items": []})
    regions = _regions_from_json(json.dumps(regions_value))
    messages_value = diagnostic_value.get("messages", {"$type": "tuple", "items": []})
    contradictions_value = diagnostic_value.get(
        "contradictory_fields", {"$type": "tuple", "items": []}
    )
    return EvidenceEnvelope(
        evidence_id=str(row["evidence_id"]),
        frame_id=str(row["frame_id"]),
        harness_id=str(row["harness_id"]),
        parser_version=str(row["parser_version"]),
        captured_at=datetime.fromisoformat(str(row["captured_at"])),
        evidence_type=str(row["evidence_type"]),
        payload=_payload_dict(str(row["payload_json"])),
        source_regions=_regions_from_json(str(row["source_regions_json"])),
        diagnostics=EvidenceDiagnostics(
            parser_name=str(diagnostic_value.get("parser_name", "unknown")),
            messages=_tuple_strings(messages_value),
            unrecognized_regions=regions,
            contradictory_fields=_tuple_strings(contradictions_value),
        ),
    )


def _payload_dict(raw: str) -> dict[str, Any]:
    value = _json_load(raw)
    return value if isinstance(value, dict) else {"value": value}


def _tuple_strings(value: object) -> tuple[str, ...]:
    items = value.get("items", []) if isinstance(value, dict) else value
    return tuple(str(item) for item in items) if isinstance(items, list) else ()


@dataclass(frozen=True, slots=True)
class PersistedObservation:
    harness_id: str
    session_id: str | None
    revision: ObservationRevision
    captured_at: datetime
    snapshot: dict[str, object]
    evidence_refs: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class SemanticEventRecord:
    id: int
    harness_id: str
    session_id: str | None
    revision: ObservationRevision
    event_type: str
    payload: dict[str, object]
    evidence_refs: tuple[dict[str, object], ...]
    diagnostics: tuple[str, ...]
    captured_at: datetime


def persist_observation_snapshot(
    conn: sqlite3.Connection,
    snapshot: ObservationSnapshot,
    *,
    session_id: str | None = None,
) -> None:
    """Persist a revisioned shared snapshot with the evidence it relies on."""
    refs = _snapshot_evidence_refs(snapshot)
    conn.execute(
        """
        INSERT INTO harness_control_observations(
            harness_id, session_id, pane_epoch, capture_sequence, semantic_sequence,
            captured_at, snapshot_json, evidence_refs_json, stored_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(harness_id, session_id, pane_epoch, capture_sequence, semantic_sequence)
        DO UPDATE SET snapshot_json=excluded.snapshot_json,
                      evidence_refs_json=excluded.evidence_refs_json,
                      captured_at=excluded.captured_at,
                      stored_at=excluded.stored_at
        """,
        (
            str(snapshot.harness_id),
            _session_key(session_id),
            snapshot.revision.pane_epoch,
            snapshot.revision.capture_sequence,
            snapshot.revision.semantic_sequence,
            snapshot.captured_at.isoformat(),
            _json_dump(snapshot),
            _json_dump(refs),
            _now(),
        ),
    )


def persist_observation_delta(
    conn: sqlite3.Connection,
    *,
    harness_id: str,
    session_id: str | None,
    revision: ObservationRevision,
    captured_at: datetime,
    delta: ObservationDelta,
) -> list[int]:
    """Persist semantic events that are intentionally outside snapshot state."""
    inserted: list[int] = []
    refs = _json_dump(delta.evidence_refs)
    diagnostics = _json_dump(delta.diagnostics)
    for event in delta.semantic_events:
        event_type = str(event.get("type", event.get("event_type", "unknown")))
        cur = conn.execute(
            """
            INSERT INTO harness_control_semantic_events(
                harness_id, session_id, pane_epoch, capture_sequence, semantic_sequence,
                event_type, payload_json, evidence_refs_json, diagnostics_json,
                captured_at, stored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                harness_id,
                _session_key(session_id),
                revision.pane_epoch,
                revision.capture_sequence,
                revision.semantic_sequence,
                event_type,
                _json_dump(event),
                refs,
                diagnostics,
                captured_at.isoformat(),
                _now(),
            ),
        )
        inserted.append(int(cur.lastrowid))
    return inserted


def latest_observation(
    conn: sqlite3.Connection, *, harness_id: str, session_id: str | None = None
) -> PersistedObservation | None:
    row = conn.execute(
        """
        SELECT * FROM harness_control_observations
         WHERE harness_id = ? AND session_id = ?
         ORDER BY pane_epoch DESC, capture_sequence DESC, semantic_sequence DESC LIMIT 1
        """,
        (harness_id, _session_key(session_id)),
    ).fetchone()
    return _observation_from_row(row) if row is not None else None


def latest_observation_snapshot(
    conn: sqlite3.Connection, *, harness_id: str, session_id: str | None = None
) -> ObservationSnapshot | None:
    """Strictly reconstruct the latest normalized snapshot for restart hydration."""
    row = conn.execute(
        """
        SELECT * FROM harness_control_observations
         WHERE harness_id = ? AND session_id = ?
         ORDER BY pane_epoch DESC, capture_sequence DESC, semantic_sequence DESC LIMIT 1
        """,
        (harness_id, _session_key(session_id)),
    ).fetchone()
    if row is None:
        return None
    value = _decode_observation_value(_json_load(str(row["snapshot_json"])))
    if not isinstance(value, ObservationSnapshot):
        raise ValueError("snapshot: persisted root is not ObservationSnapshot")
    revision = ObservationRevision(
        int(row["pane_epoch"]), int(row["capture_sequence"]), int(row["semantic_sequence"])
    )
    if (
        str(value.harness_id) != harness_id
        or value.revision != revision
        or value.captured_at != datetime.fromisoformat(str(row["captured_at"]))
    ):
        raise ValueError("snapshot: typed payload contradicts indexed observation columns")
    return value


def list_session_evidence(
    conn: sqlite3.Connection, *, harness_id: str, session_id: str | None = None
) -> tuple[EvidenceEnvelope, ...]:
    """Load retained parser history for one pane session in capture order."""
    session_clause = "frame.session_id IS NULL" if session_id is None else "frame.session_id = ?"
    params: tuple[str, ...] = (harness_id,) if session_id is None else (harness_id, session_id)
    rows = conn.execute(
        f"""
        SELECT evidence.*
          FROM harness_control_evidence AS evidence
          JOIN harness_control_frames AS frame ON frame.frame_id = evidence.frame_id
         WHERE evidence.harness_id = ? AND {session_clause}
         ORDER BY frame.pane_epoch, frame.capture_sequence, evidence.evidence_id
        """,
        params,
    ).fetchall()
    return tuple(_evidence_from_row(row) for row in rows)


def _observation_from_row(row: sqlite3.Row) -> PersistedObservation:
    refs = _json_load(str(row["evidence_refs_json"]))
    ref_items = refs.get("items", []) if isinstance(refs, dict) else refs
    return PersistedObservation(
        harness_id=str(row["harness_id"]),
        session_id=str(row["session_id"]) or None,
        revision=ObservationRevision(
            int(row["pane_epoch"]), int(row["capture_sequence"]), int(row["semantic_sequence"])
        ),
        captured_at=datetime.fromisoformat(str(row["captured_at"])),
        snapshot=_payload_dict(str(row["snapshot_json"])),
        evidence_refs=tuple(item for item in ref_items if isinstance(item, dict)),
    )


def list_semantic_events(
    conn: sqlite3.Connection, *, harness_id: str, session_id: str | None = None
) -> list[SemanticEventRecord]:
    rows = conn.execute(
        "SELECT * FROM harness_control_semantic_events "
        "WHERE harness_id = ? AND session_id = ? ORDER BY id",
        (harness_id, _session_key(session_id)),
    ).fetchall()
    records: list[SemanticEventRecord] = []
    for row in rows:
        refs = _json_load(str(row["evidence_refs_json"]))
        ref_items = refs.get("items", []) if isinstance(refs, dict) else refs
        diagnostics = _tuple_strings(_json_load(str(row["diagnostics_json"])))
        records.append(
            SemanticEventRecord(
                id=int(row["id"]),
                harness_id=str(row["harness_id"]),
                session_id=str(row["session_id"]) or None,
                revision=ObservationRevision(
                    int(row["pane_epoch"]),
                    int(row["capture_sequence"]),
                    int(row["semantic_sequence"]),
                ),
                event_type=str(row["event_type"]),
                payload=_payload_dict(str(row["payload_json"])),
                evidence_refs=tuple(item for item in ref_items if isinstance(item, dict)),
                diagnostics=diagnostics,
                captured_at=datetime.fromisoformat(str(row["captured_at"])),
            )
        )
    return records


def _snapshot_evidence_refs(snapshot: ObservationSnapshot) -> tuple[EvidenceRef, ...]:
    refs: dict[tuple[str, str, tuple[ScreenRegionRef, ...]], EvidenceRef] = {}
    for field_name in (
        "surface",
        "composer",
        "generation",
        "transcript_tail",
        "modal",
        "question",
        "permission_request",
        "active_model",
        "model_configuration",
        "settings",
        "usage",
        "tool_activity",
    ):
        for ref in getattr(snapshot, field_name).evidence:
            refs[(str(ref.evidence_id), str(ref.frame_id), ref.source_regions)] = ref
    return tuple(refs.values())


@dataclass(frozen=True, slots=True)
class PersistedOperation:
    operation_id: str
    harness_id: str
    session_id: str | None
    capability: str
    status: str
    phase_type: str
    phase_payload: dict[str, object]
    request: dict[str, object]
    operation_state: dict[str, object]
    created_at: datetime
    updated_at: datetime
    deadline: datetime | None
    attempt_count: int
    last_observation_revision: ObservationRevision | None
    warnings: object


@dataclass(frozen=True, slots=True)
class PersistedAction:
    action_id: str
    operation_id: str
    duplicate_policy: str
    emission_status: str
    effect_statuses: tuple[str, ...]

    @property
    def unsafe_emission_ambiguous(self) -> bool:
        return self.duplicate_policy in {
            "AMBIGUOUS_AFTER_EMISSION",
            "NEVER_AUTOMATICALLY_REPLAY",
        } and any(status in {"PENDING", "EMITTED", "FAILED"} for status in self.effect_statuses)


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    operation: PersistedOperation
    latest_observation: PersistedObservation | None
    actions: tuple[PersistedAction, ...]

    @property
    def has_ambiguous_unsafe_effect(self) -> bool:
        return any(action.unsafe_emission_ambiguous for action in self.actions)


def persist_operation(
    conn: sqlite3.Connection,
    envelope: OperationEnvelope[object],
    *,
    harness_id: str,
    session_id: str | None = None,
    request: object | None = None,
    operation_state: object | None = None,
) -> None:
    """Persist a capability operation snapshot independently of a call stack."""
    revision = envelope.last_observation_revision
    phase_payload = _json_dump(envelope.phase)
    request_payload = _json_dump(request if request is not None else {})
    state_payload = _json_dump(operation_state if operation_state is not None else {})
    conn.execute(
        """
        INSERT INTO harness_control_operations(
            operation_id, harness_id, session_id, capability, status, phase_type,
            phase_payload_json, request_json, operation_state_json, created_at, updated_at,
            deadline, attempt_count, last_pane_epoch, last_capture_sequence,
            last_semantic_sequence, warnings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(operation_id) DO UPDATE SET
            harness_id=excluded.harness_id, session_id=excluded.session_id,
            capability=excluded.capability, status=excluded.status, phase_type=excluded.phase_type,
            phase_payload_json=excluded.phase_payload_json, request_json=excluded.request_json,
            operation_state_json=excluded.operation_state_json, updated_at=excluded.updated_at,
            deadline=excluded.deadline, attempt_count=excluded.attempt_count,
            last_pane_epoch=excluded.last_pane_epoch,
            last_capture_sequence=excluded.last_capture_sequence,
            last_semantic_sequence=excluded.last_semantic_sequence,
            warnings_json=excluded.warnings_json
        """,
        (
            str(envelope.operation_id),
            harness_id,
            session_id,
            envelope.capability,
            envelope.status.name,
            _type_name(envelope.phase),
            phase_payload,
            request_payload,
            state_payload,
            envelope.created_at.isoformat(),
            envelope.updated_at.isoformat(),
            envelope.deadline.isoformat() if envelope.deadline else None,
            envelope.attempt_count,
            revision.pane_epoch if revision else None,
            revision.capture_sequence if revision else None,
            revision.semantic_sequence if revision else None,
            _json_dump(envelope.warnings),
        ),
    )


def get_operation(conn: sqlite3.Connection, operation_id: str) -> PersistedOperation | None:
    row = conn.execute(
        "SELECT * FROM harness_control_operations WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    return _operation_from_row(row) if row is not None else None


def persist_action_record(conn: sqlite3.Connection, record: ActionRecord) -> None:
    """Atomically write action intent and all lowered effects before emission."""
    if get_operation(conn, str(record.operation_id)) is None:
        raise ValueError(f"action {record.action_id!r} references an unknown operation")
    existing = conn.execute(
        "SELECT operation_id, semantic_action_json "
        "FROM harness_control_actions WHERE action_id = ?",
        (str(record.action_id),),
    ).fetchone()
    action_json = _json_dump(record.semantic_action)
    if existing is not None:
        if (
            str(existing["operation_id"]) != str(record.operation_id)
            or str(existing["semantic_action_json"]) != action_json
        ):
            raise ValueError(f"action id {record.action_id!r} already identifies different intent")
        return
    conn.execute(
        """
        INSERT INTO harness_control_actions(
            action_id, operation_id, semantic_action_type, semantic_action_json, duplicate_policy,
            selected_pane_epoch, selected_capture_sequence, selected_semantic_sequence,
            requested_at, expectation_json, emitted_at, emission_error, emission_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(record.action_id),
            str(record.operation_id),
            _type_name(record.semantic_action),
            action_json,
            record.duplicate_policy.name,
            record.selected_from_revision.pane_epoch,
            record.selected_from_revision.capture_sequence,
            record.selected_from_revision.semantic_sequence,
            record.requested_at.isoformat(),
            _json_dump(record.expectation),
            record.emitted_at.isoformat() if record.emitted_at else None,
            record.emission_error,
            "FAILED" if record.emission_error else ("EMITTED" if record.emitted_at else "PENDING"),
        ),
    )
    for ordinal, effect in enumerate(record.lowered_effects):
        conn.execute(
            """
            INSERT INTO harness_control_effects(
                effect_id, action_id, effect_type, payload_json, ordinal, emission_status
            ) VALUES (?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                str(effect.effect_id),
                str(record.action_id),
                _type_name(effect),
                _json_dump(effect),
                ordinal,
            ),
        )


def record_effect_emissions(
    conn: sqlite3.Connection,
    *,
    action_id: str,
    results: Iterable[EffectEmission],
    emitted_at: datetime,
) -> None:
    """Record tmux acceptance/failure only after an action was durably selected."""
    action = conn.execute(
        "SELECT action_id FROM harness_control_actions WHERE action_id = ?", (action_id,)
    ).fetchone()
    if action is None:
        raise ValueError(f"cannot record emission for unknown action {action_id!r}")
    results = tuple(results)
    for result in results:
        updated = conn.execute(
            """
            UPDATE harness_control_effects
               SET emission_status = ?, emitted_at = ?, emission_error = ?
             WHERE effect_id = ? AND action_id = ?
            """,
            (
                result.status.name,
                emitted_at.isoformat(),
                result.error,
                str(result.effect_id),
                action_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError(f"effect {result.effect_id!r} is not part of action {action_id!r}")
    statuses = [
        str(row["emission_status"])
        for row in conn.execute(
            "SELECT emission_status FROM harness_control_effects WHERE action_id = ?", (action_id,)
        ).fetchall()
    ]
    action_status = (
        "FAILED"
        if "FAILED" in statuses
        else (
            "EMITTED" if statuses and all(status == "EMITTED" for status in statuses) else "PENDING"
        )
    )
    error = next((result.error for result in results if result.error), None)
    conn.execute(
        """
        UPDATE harness_control_actions
           SET emitted_at = ?, emission_error = COALESCE(?, emission_error), emission_status = ?
         WHERE action_id = ?
        """,
        (emitted_at.isoformat(), error, action_status, action_id),
    )


def persist_decision_record(conn: sqlite3.Connection, record: DecisionRecord) -> int:
    if get_operation(conn, str(record.operation_id)) is None:
        raise ValueError(f"decision references an unknown operation {record.operation_id!r}")
    cur = conn.execute(
        """
        INSERT INTO harness_control_decisions(
            operation_id, pane_epoch, capture_sequence, semantic_sequence, phase_before,
            predicate_results_json, selected_decision, selected_action_id, reason, decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(record.operation_id),
            record.observation_revision.pane_epoch,
            record.observation_revision.capture_sequence,
            record.observation_revision.semantic_sequence,
            record.phase_before,
            _json_dump(record.predicate_results),
            record.selected_decision.name,
            str(record.selected_action_id) if record.selected_action_id is not None else None,
            record.reason,
            record.decided_at.isoformat(),
        ),
    )
    return int(cur.lastrowid)


def load_recovery_candidates(
    conn: sqlite3.Connection,
    *,
    harness_id: str,
    session_id: str | None = None,
) -> list[RecoveryCandidate]:
    """Load unfinished semantic work with current observations, never a Python stack.

    A caller reconciles the returned operation state against ``latest_observation``.
    `has_ambiguous_unsafe_effect` explicitly flags actions that must not be
    replayed merely because the process restarted.
    """
    params: list[str] = [harness_id]
    session_clause = "session_id IS NULL" if session_id is None else "session_id = ?"
    if session_id is not None:
        params.append(session_id)
    rows = conn.execute(
        f"""
        SELECT * FROM harness_control_operations
         WHERE harness_id = ? AND {session_clause} AND status IN ('PENDING', 'RUNNING')
         ORDER BY created_at, operation_id
        """,
        params,
    ).fetchall()
    latest = latest_observation(conn, harness_id=harness_id, session_id=session_id)
    candidates: list[RecoveryCandidate] = []
    for row in rows:
        operation = _operation_from_row(row)
        actions = _actions_for_operation(conn, operation.operation_id)
        candidates.append(RecoveryCandidate(operation, latest, tuple(actions)))
    return candidates


def escalate_recovery_candidate(
    conn: sqlite3.Connection, *, operation_id: str, reason: str, observed_at: datetime
) -> None:
    """Durably close unfinished work after a new restart observation.

    Reconstructing a procedural call stack would violate replay safety.  The
    candidate's operation/action/effect history remains queryable, while this
    explicit escalation prevents a later startup from blindly re-emitting it.
    """

    row = conn.execute(
        "SELECT warnings_json FROM harness_control_operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown recovery operation {operation_id!r}")
    warnings = _json_load(str(row["warnings_json"]))
    items = warnings if isinstance(warnings, list) else []
    items.append({"recovery_escalation": reason, "observed_at": observed_at.isoformat()})
    conn.execute(
        """
        UPDATE harness_control_operations
           SET status = 'ESCALATED', updated_at = ?, warnings_json = ?
         WHERE operation_id = ? AND status IN ('PENDING', 'RUNNING')
        """,
        (observed_at.isoformat(), _json_dump(items), operation_id),
    )


def _operation_from_row(row: sqlite3.Row) -> PersistedOperation:
    revision = None
    if row["last_pane_epoch"] is not None:
        revision = ObservationRevision(
            int(row["last_pane_epoch"]),
            int(row["last_capture_sequence"]),
            int(row["last_semantic_sequence"]),
        )
    return PersistedOperation(
        operation_id=str(row["operation_id"]),
        harness_id=str(row["harness_id"]),
        session_id=str(row["session_id"]) if row["session_id"] is not None else None,
        capability=str(row["capability"]),
        status=str(row["status"]),
        phase_type=str(row["phase_type"]),
        phase_payload=_payload_dict(str(row["phase_payload_json"])),
        request=_payload_dict(str(row["request_json"])),
        operation_state=_payload_dict(str(row["operation_state_json"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        deadline=datetime.fromisoformat(str(row["deadline"])) if row["deadline"] else None,
        attempt_count=int(row["attempt_count"]),
        last_observation_revision=revision,
        warnings=_json_load(str(row["warnings_json"])),
    )


def _actions_for_operation(conn: sqlite3.Connection, operation_id: str) -> list[PersistedAction]:
    rows = conn.execute(
        "SELECT * FROM harness_control_actions "
        "WHERE operation_id = ? ORDER BY requested_at, action_id",
        (operation_id,),
    ).fetchall()
    actions: list[PersistedAction] = []
    for row in rows:
        effects = conn.execute(
            "SELECT emission_status FROM harness_control_effects "
            "WHERE action_id = ? ORDER BY ordinal",
            (str(row["action_id"]),),
        ).fetchall()
        actions.append(
            PersistedAction(
                action_id=str(row["action_id"]),
                operation_id=operation_id,
                duplicate_policy=str(row["duplicate_policy"]),
                emission_status=str(row["emission_status"]),
                effect_statuses=tuple(str(effect["emission_status"]) for effect in effects),
            )
        )
    return actions


__all__ = [
    "PersistedAction",
    "PersistedObservation",
    "PersistedOperation",
    "RecoveryCandidate",
    "SemanticEventRecord",
    "get_evidence",
    "get_frame",
    "get_operation",
    "latest_observation",
    "latest_observation_snapshot",
    "list_evidence",
    "list_session_evidence",
    "list_semantic_events",
    "load_recovery_candidates",
    "escalate_recovery_candidate",
    "persist_action_record",
    "persist_decision_record",
    "persist_evidence",
    "persist_frame",
    "persist_observation_delta",
    "persist_observation_snapshot",
    "persist_operation",
    "record_effect_emissions",
]
