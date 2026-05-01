"""Plan schema and parser. Plans are mostly free-form prose (per
furtherspecproposal §6); this package keeps frontmatter parsing and
canonicalization."""

from murder.plans.schema import Plan, PlanStatus

__all__ = ["Plan", "PlanStatus"]
