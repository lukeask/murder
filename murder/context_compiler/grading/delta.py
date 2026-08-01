"""Apply ``RequestDelta`` hints into an existing ``ContextRequest``.

All delta fields feed the ordinary retrieval path: path/symbol hints,
``search_terms``, and ``relationship_kind_hints``. Providers and Step 4
expansion read those request fields — no parallel operation vocabulary.
"""

from __future__ import annotations

from dataclasses import replace

from murder.context_compiler.grading.models import RequestDelta
from murder.context_compiler.grading.policy import (
    ALLOWED_RELATIONSHIP_KINDS,
    MAX_DELTA_PATH_HINTS,
    MAX_DELTA_RELATIONSHIP_KINDS,
    MAX_DELTA_SEARCH_TERMS,
    MAX_DELTA_SYMBOL_HINTS,
    MAX_UNRESOLVED_QUESTIONS,
)
from murder.context_compiler.models import ContextRequest


def _dedupe_cap(values: tuple[str, ...], *, cap: int) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item or item in out:
            continue
        out.append(item)
        if len(out) >= cap:
            break
    return tuple(out)


def bound_delta(delta: RequestDelta) -> RequestDelta:
    """Clamp every list in ``delta`` to policy caps and allowed kinds."""
    kinds = tuple(
        k
        for k in _dedupe_cap(delta.relationship_kinds, cap=MAX_DELTA_RELATIONSHIP_KINDS)
        if k in ALLOWED_RELATIONSHIP_KINDS
    )
    return RequestDelta(
        path_hints=_dedupe_cap(delta.path_hints, cap=MAX_DELTA_PATH_HINTS),
        symbol_hints=_dedupe_cap(delta.symbol_hints, cap=MAX_DELTA_SYMBOL_HINTS),
        search_terms=_dedupe_cap(delta.search_terms, cap=MAX_DELTA_SEARCH_TERMS),
        relationship_kinds=kinds,
        unresolved_questions=_dedupe_cap(delta.unresolved_questions, cap=MAX_UNRESOLVED_QUESTIONS),
    )


def apply_request_delta(request: ContextRequest, delta: RequestDelta) -> ContextRequest:
    """Return a new request with every delta field merged for re-propose."""
    bounded = bound_delta(delta)
    path_hints = _dedupe_cap(
        (*request.path_hints, *bounded.path_hints),
        cap=MAX_DELTA_PATH_HINTS + len(request.path_hints),
    )
    symbol_hints = _dedupe_cap(
        (*request.symbol_hints, *bounded.symbol_hints),
        cap=MAX_DELTA_SYMBOL_HINTS + len(request.symbol_hints),
    )
    search_terms = _dedupe_cap(
        (*request.search_terms, *bounded.search_terms),
        cap=MAX_DELTA_SEARCH_TERMS + len(request.search_terms),
    )
    relationship_kind_hints = _dedupe_cap(
        (*request.relationship_kind_hints, *bounded.relationship_kinds),
        cap=MAX_DELTA_RELATIONSHIP_KINDS + len(request.relationship_kind_hints),
    )
    return replace(
        request,
        path_hints=path_hints,
        symbol_hints=symbol_hints,
        search_terms=search_terms,
        relationship_kind_hints=relationship_kind_hints,
    )


__all__ = [
    "apply_request_delta",
    "bound_delta",
]
