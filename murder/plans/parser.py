"""Plan markdown parser/writer.

YAML frontmatter + free-form body. v0 keeps it light — Collaborator
mostly edits these directly through CC's file tools.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from murder.plans.schema import Plan, PlanStatus

_FRONTMATTER_DELIM = "---"


def parse(md_text: str) -> Plan:
    """Parse a YAML-frontmatter + body markdown file into a Plan."""
    # TODO(M5): split on _FRONTMATTER_DELIM; yaml.safe_load the front; body = remainder.
    raise NotImplementedError("M5: plans.parser.parse")


def render(plan: Plan) -> str:
    """Emit a plan back to canonical YAML-frontmatter markdown."""
    # TODO(M5): yaml.safe_dump(metadata) between delims, body trailing.
    raise NotImplementedError("M5: plans.parser.render")


def read(path: Path) -> Plan:
    return parse(path.read_text(encoding="utf-8"))


def write(path: Path, plan: Plan) -> None:
    # TODO(M5): atomic write via tempfile + os.replace.
    raise NotImplementedError("M5: plans.parser.write")


def new_plan(name: str) -> Plan:
    return Plan(name=name, created_at=datetime.utcnow(), status=PlanStatus.DRAFT)
