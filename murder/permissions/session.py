"""Normalization and enforcement adapters for live session side effects."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from murder.permissions.contracts import (
    AuthorizationProof,
    PermissionPrincipal,
    ProposedOperation,
    SessionControl,
    TerminalWrite,
    WriterTakeover,
)
from murder.permissions.service import PermissionService

if TYPE_CHECKING:
    from murder.runtime.sessions.contracts import (
        AcquireWriterLease,
        HarnessSessionRecord,
        PrincipalRef,
        SessionCommand,
        WriterLease,
    )


def normalize_session_command(
    command: SessionCommand,
    record: HarnessSessionRecord,
    principal: PrincipalRef,
) -> ProposedOperation:
    actor = PermissionPrincipal.model_validate(principal.model_dump())
    if command.type == "write_terminal_input":
        data = command.data.encode("utf-8")
        return TerminalWrite(
            operation_id=command.operation_id,
            principal=actor,
            session_id=record.session_id,
            encoding=command.encoding,
            data_digest=hashlib.sha256(data).hexdigest(),
            byte_count=len(data),
            lease_id=command.lease_id,
            lease_fence=command.fence,
        )
    return SessionControl(
        operation_id=command.operation_id,
        principal=actor,
        session_id=record.session_id,
        command=command.type,
        arguments_digest=_digest(command.model_dump(mode="json")),
    )


def normalize_writer_takeover(
    request: AcquireWriterLease,
    holder: PrincipalRef,
    current_lease: WriterLease,
) -> ProposedOperation:
    return WriterTakeover(
        operation_id=request.meta.request_id,
        principal=PermissionPrincipal.model_validate(holder.model_dump()),
        session_id=request.session_id,
        requested_mode=request.mode.value,
        current_lease_id=current_lease.lease_id,
        current_lease_fence=current_lease.fence,
        current_holder=PermissionPrincipal.model_validate(
            current_lease.holder.model_dump()
        ),
        request_digest=_digest(request.model_dump(mode="json")),
    )


class SessionPermissionAuthorizer:
    """Validate a proof as the controller's final guard before physical I/O."""

    def __init__(self, service: PermissionService) -> None:
        self._service = service

    async def __call__(
        self,
        command: SessionCommand,
        record: HarnessSessionRecord,
        principal: PrincipalRef,
        authorization: AuthorizationProof | None,
    ) -> bool:
        return self._service.authorize_or_enforce(
            normalize_session_command(command, record, principal),
            authorization,
        )

    def authorize_takeover(
        self,
        request: AcquireWriterLease,
        *,
        holder: PrincipalRef,
        current_lease: WriterLease,
        authorization: AuthorizationProof | None,
    ) -> bool:
        return self._service.authorize_or_enforce(
            normalize_writer_takeover(request, holder, current_lease),
            authorization,
        )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "SessionPermissionAuthorizer",
    "normalize_session_command",
    "normalize_writer_takeover",
]
