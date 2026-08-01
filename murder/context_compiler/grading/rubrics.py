"""Profile-specific grading rubrics (prompt text only).

Rubrics guide judgement; they do not grant the model authority over ranges,
operations, or recipient-facing prose.
"""

from __future__ import annotations

from murder.context_compiler.models import RecipientProfile

_SHARED = """\
You grade a bounded list of exact source ranges for one coding request.
For each proposal index decide include true/false, an evidence category, and a
reason_code from the closed enum. Adequacy is part of the same answer: if the
set is missing something essential, populate gaps with path_hints, symbol_hints,
search_terms, and/or relationship_kinds. Do not invent operations. Do not invent
paths or indices that are not in the proposal list. Do not write recipient prose.
One short rationale sentence per grade is optional and for internal tracing only —
never emit chain-of-thought.
Categories: edit_target, contract, supporting_context, test, verification,
current_diff, other.
Reason codes: likely_edit_target, required_contract, direct_caller, direct_callee,
focused_test, nonlocal_consequence, framework_resource, configuration_owner,
task_irrelevant, duplicate_information, too_weak, oversized_low_value.
"""

_COMPACT = """\
Profile: compact.
Prefer the edit owner and the minimum contract needed to act. Exclude structural
hubs, distant callers, and broad subsystem tours. Keep at most one focused test
when it directly exercises the edit. Prefer gaps only when the edit target itself
is absent.
"""

_IMPLEMENTATION = """\
Profile: implementation.
Keep edit targets, contracts they call, direct callers/callees, and the focused
test that covers the change. Exclude structurally central but task-irrelevant
modules. Prefer one clear test over several weak ones. Request gaps when a direct
caller or focused test is obviously missing from the proposal.
"""

_PLANNING = """\
Profile: planning.
Prefer broader public contracts, ownership boundaries, and consumer edges over a
single edit site. Include configuration owners and framework resources when they
define the subsystem. Still exclude hubs that are only popular, not relevant.
Gaps may ask for contracts or consumers the proposal omitted.
"""


def rubric_for_profile(profile: RecipientProfile) -> str:
    """Return the rubric body for ``profile``."""
    if profile is RecipientProfile.COMPACT:
        body = _COMPACT
    elif profile is RecipientProfile.PLANNING:
        body = _PLANNING
    elif profile is RecipientProfile.IMPLEMENTATION:
        body = _IMPLEMENTATION
    else:
        raise ValueError(f"unknown recipient profile: {profile!r}")
    return f"{_SHARED}\n{body}".strip()


__all__ = [
    "rubric_for_profile",
]
