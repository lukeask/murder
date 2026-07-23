"""Unit tests for shared domain/wire request-id bridging."""

from __future__ import annotations

from uuid import UUID, uuid4

from murder.contracts.common import (
    domain_request_id,
    request_context,
    try_parse_domain_request_id,
)


def test_try_parse_domain_request_id_accepts_bare_and_prefixed() -> None:
    bare = "550e8400-e29b-41d4-a716-446655440000"
    assert try_parse_domain_request_id(bare) == UUID(bare)
    assert try_parse_domain_request_id(f"request-{bare}") == UUID(bare)
    assert try_parse_domain_request_id(f"req-{bare}") == UUID(bare)
    assert try_parse_domain_request_id("q-1") is None
    assert try_parse_domain_request_id("request-not-a-uuid") is None
    assert try_parse_domain_request_id("  ") is None


def test_domain_request_id_prefers_explicit_then_wire_then_fresh() -> None:
    explicit = uuid4()
    wire = "550e8400-e29b-41d4-a716-446655440000"
    assert (
        domain_request_id(
            explicit=explicit,
            wire_request_id=f"request-{wire}",
        )
        == explicit
    )
    assert domain_request_id(wire_request_id=f"request-{wire}") == UUID(wire)
    generated = domain_request_id(wire_request_id="health-1")
    assert isinstance(generated, UUID)
    assert generated != UUID(wire)


def test_request_context_correlates_on_request_id() -> None:
    ctx = request_context(
        wire_request_id="request-550e8400-e29b-41d4-a716-446655440000",
        expected_revision=3,
    )
    assert ctx.request_id == UUID("550e8400-e29b-41d4-a716-446655440000")
    assert ctx.correlation.correlation_id == ctx.request_id
    assert ctx.expected_revision == 3
