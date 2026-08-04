"""Service-owned live harness session contracts and lazily loaded controllers."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    HarnessSessionRecord,
    InterruptSession,
    PrincipalKind,
    PrincipalRef,
    ReleaseWriterLease,
    RenewWriterLease,
    ResizeTerminal,
    SendStructuredMessage,
    SessionCapabilities,
    SessionCommand,
    SessionStatus,
    SessionTransport,
    TerminateSession,
    WriterLease,
    WriterLeaseDenied,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)

if TYPE_CHECKING:
    from murder.runtime.sessions.backend import TmuxSessionBackend
    from murder.runtime.sessions.capabilities import verified_tmux_capabilities
    from murder.runtime.sessions.controller import SessionController
    from murder.runtime.sessions.persistence import (
        SESSION_SCHEMA_SQL,
        SessionStore,
        ensure_session_schema,
    )
    from murder.runtime.sessions.registry import SessionControllerRegistry


_LAZY_EXPORTS = {
    "SESSION_SCHEMA_SQL": ("murder.runtime.sessions.persistence", "SESSION_SCHEMA_SQL"),
    "SessionController": ("murder.runtime.sessions.controller", "SessionController"),
    "SessionControllerRegistry": (
        "murder.runtime.sessions.registry",
        "SessionControllerRegistry",
    ),
    "SessionStore": ("murder.runtime.sessions.persistence", "SessionStore"),
    "TmuxSessionBackend": ("murder.runtime.sessions.backend", "TmuxSessionBackend"),
    "ensure_session_schema": ("murder.runtime.sessions.persistence", "ensure_session_schema"),
    "verified_tmux_capabilities": (
        "murder.runtime.sessions.capabilities",
        "verified_tmux_capabilities",
    ),
}


def __getattr__(name: str) -> Any:
    """Avoid initializing persistence when callers import session wire contracts."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    "AcquireWriterLease",
    "HarnessSessionRecord",
    "InterruptSession",
    "PrincipalKind",
    "PrincipalRef",
    "ReleaseWriterLease",
    "RenewWriterLease",
    "ResizeTerminal",
    "SESSION_SCHEMA_SQL",
    "SendStructuredMessage",
    "SessionCapabilities",
    "SessionCommand",
    "SessionController",
    "SessionControllerRegistry",
    "SessionStatus",
    "SessionStore",
    "SessionTransport",
    "TerminateSession",
    "TmuxSessionBackend",
    "WriteTerminalInput",
    "WriterLease",
    "WriterLeaseDenied",
    "WriterLeaseGranted",
    "WriterMode",
    "ensure_session_schema",
    "verified_tmux_capabilities",
]
