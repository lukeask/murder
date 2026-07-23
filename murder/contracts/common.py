"""Shared identity and request envelope primitives.

Sessions, workflows, permissions, and facts must agree on who acted and how a
request is correlated. Feature modules may narrow these types with validators or
aliases; they must not redefine nominal copies that drift independently.

Transport handles (wire ``request_id``, ``subscription_id``, ``stream_id``) remain
opaque strings at the socket edge. Domain correlation uses ``UUID``. Bridge once
at the gateway with :func:`domain_request_id`.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# Known client prefixes for wire RPC correlation IDs. Stripped only when deriving
# a domain request UUID; transport handles themselves stay opaque strings.
_WIRE_REQUEST_ID_PREFIXES = ("request-", "req-")


class StrEnum(str, Enum):
    """Python 3.10 compatible string enum."""

    def __str__(self) -> str:
        return str.__str__(self)


class ContractModel(BaseModel):
    """Immutable public or persistence contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PrincipalKind(StrEnum):
    """Who may be attributed as an actor or authorization subject.

    ``llm`` is a principal when an agent proposes or executes work under policy.
    An LLM safety *review* remains evidence (see permissions), not authority by
    virtue of this kind alone.
    """

    USER = "user"
    CLIENT = "client"
    WORKFLOW = "workflow"
    SERVICE = "service"
    REVIEWER = "reviewer"
    LLM = "llm"


class Principal(ContractModel):
    """Stable reference to an acting or authorized subject."""

    kind: PrincipalKind
    id: str = Field(min_length=1)


class Causation(ContractModel):
    """Immediate cause of an action, fact, or follow-on request."""

    causation_id: UUID


class Correlation(ContractModel):
    """Cross-cutting request / work / fact linkage."""

    correlation_id: UUID
    causation_id: UUID | None = None
    trace_id: UUID | None = None

    @property
    def causation(self) -> Causation | None:
        if self.causation_id is None:
            return None
        return Causation(causation_id=self.causation_id)


class RequestContext(ContractModel):
    """Client- or service-facing request envelope metadata."""

    request_id: UUID
    correlation: Correlation
    expected_revision: int | None = None


def try_parse_domain_request_id(value: str) -> UUID | None:
    """Parse a domain UUID from a wire or params string.

    Accepts a bare UUID, or a UUID after stripping known client prefixes
    (``request-``, ``req-``). Returns ``None`` when the value is not a UUID.
    """

    text = value.strip()
    if not text:
        return None
    for prefix in _WIRE_REQUEST_ID_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    try:
        return UUID(text)
    except ValueError:
        return None


def domain_request_id(
    *,
    explicit: UUID | None = None,
    wire_request_id: str | None = None,
) -> UUID:
    """Resolve one domain request UUID for correlatable capabilities.

    Preference order: explicit params ``request_id``, then a parseable wire
    ``request_id``, otherwise a fresh ``uuid4``. Opaque non-UUID wire IDs are
    never forced into the domain namespace.
    """

    if explicit is not None:
        return explicit
    if wire_request_id is not None:
        parsed = try_parse_domain_request_id(wire_request_id)
        if parsed is not None:
            return parsed
    return uuid4()


def request_context(
    *,
    explicit_request_id: UUID | None = None,
    wire_request_id: str | None = None,
    expected_revision: int | None = None,
    causation_id: UUID | None = None,
    trace_id: UUID | None = None,
) -> RequestContext:
    """Build :class:`RequestContext` with a single correlation id."""

    rid = domain_request_id(explicit=explicit_request_id, wire_request_id=wire_request_id)
    return RequestContext(
        request_id=rid,
        correlation=Correlation(
            correlation_id=rid,
            causation_id=causation_id,
            trace_id=trace_id,
        ),
        expected_revision=expected_revision,
    )
