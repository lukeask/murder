"""Server-authoritative workflow-template compilation.

Compilation order for each templatable stage field (``title``, ``instructions``):

1. Expand inline ``:foo:`` prompt templates (single-pass; bodies are not re-scanned).
2. Collect ``{workflow-input}`` placeholders from the expanded text.
3. Merge with optional declared ``WorkflowDef.inputs`` into wizard fields + diagnostics.

The expanded template still contains unresolved ``{inputs}``; substitution happens
later at materialize time. Unknown ``:name:`` references are compile errors (unlike
chat, which leaves them literal silently).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from murder.app.protocol.common import ApplicationModel
from murder.work.workflows.definition import (
    StageDef,
    WorkflowDef,
    WorkflowInputDecl,
    WorkflowInputKind,
)

# Identifier-like names only (must start with a letter or underscore). Pure-digit
# spans like ``12:30:``, ``1:2:3``, and ``:100:`` are left literal — they are times /
# versions, not prompt-template refs.
_INLINE_TEMPLATE_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_-]*):")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_-]+)\}")

WorkflowCompileIssueCode = Literal[
    "unknown_prompt_template",
    "unused_input",
    "required_input_missing",
]


class WorkflowInput(ApplicationModel):
    """One wizard field produced by compilation (declared and/or inferred)."""

    name: str
    label: str
    kind: WorkflowInputKind = "text"
    required: bool = False
    default: str | None = None
    inferred: bool = False


class WorkflowCompileIssue(ApplicationModel):
    """A compile-time diagnostic for a workflow template."""

    code: WorkflowCompileIssueCode
    message: str
    severity: Literal["error", "warning"] = "error"
    path: list[str | int] = Field(default_factory=list)
    input_name: str | None = None
    template_name: str | None = None
    stage_id: str | None = None


class CompileWorkflowTemplateParams(ApplicationModel):
    """Params for ``workflow.compile``.

    Provide either an inline ``template`` (editor draft) or a saved registry
    ``name``. Optional ``prompt_templates`` overrides the userspace registry
    (useful for previewing unsaved prompt-template edits).
    """

    template: WorkflowDef | None = None
    name: str | None = None
    prompt_templates: dict[str, str] | None = None

    @model_validator(mode="after")
    def require_template_or_name(self) -> CompileWorkflowTemplateParams:
        if self.template is None and not (self.name and self.name.strip()):
            raise ValueError("template or name is required")
        if self.name is not None:
            text = self.name.strip()
            if not text:
                raise ValueError("name must be non-empty")
            self.name = text
        return self


class CompileWorkflowTemplateResult(ApplicationModel):
    """Compile result: expanded template, wizard inputs, and diagnostics."""

    ok: bool
    expanded_template: WorkflowDef
    inputs: list[WorkflowInput] = Field(default_factory=list)
    issues: list[WorkflowCompileIssue] = Field(default_factory=list)


def expand_inline_prompt_templates(
    text: str,
    templates: Mapping[str, str],
) -> tuple[str, list[str]]:
    """Single-pass expand of inline ``:name:`` macros.

    Returns ``(expanded_text, unknown_names)``. Unknown names are left verbatim
    in the expanded text and listed in first-occurrence order (deduplicated).
    Replacement bodies are not re-scanned.
    """
    unknown: list[str] = []
    seen_unknown: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        body = templates.get(name)
        if body is not None:
            return body
        if name not in seen_unknown:
            seen_unknown.add(name)
            unknown.append(name)
        return match.group(0)

    return _INLINE_TEMPLATE_RE.sub(_replace, text), unknown


def collect_placeholders(*texts: str) -> list[str]:
    """Return distinct ``{name}`` tokens in first-occurrence order across *texts*."""
    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _PLACEHOLDER_RE.finditer(text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def compile_workflow_template(
    template: WorkflowDef,
    *,
    prompt_templates: Mapping[str, str] | None = None,
) -> CompileWorkflowTemplateResult:
    """Compile *template*: expand ``:foo:``, collect inputs, emit diagnostics."""
    registry = dict(prompt_templates or {})
    issues: list[WorkflowCompileIssue] = []
    expanded_stages: list[StageDef] = []
    discovered: list[str] = []
    discovered_seen: set[str] = set()

    for stage_index, stage in enumerate(template.stages):
        title, title_unknown = expand_inline_prompt_templates(stage.title, registry)
        instructions, instr_unknown = expand_inline_prompt_templates(
            stage.instructions, registry
        )
        unknown_fields: dict[str, str] = {}
        for unknown_name in title_unknown:
            unknown_fields.setdefault(unknown_name, "title")
        for unknown_name in instr_unknown:
            unknown_fields.setdefault(unknown_name, "instructions")
        for unknown_name, field in unknown_fields.items():
            issues.append(
                WorkflowCompileIssue(
                    code="unknown_prompt_template",
                    message=(
                        f"stage {stage.id!r} references unknown prompt template "
                        f":{unknown_name}:"
                    ),
                    severity="error",
                    path=["stages", stage_index, field],
                    template_name=unknown_name,
                    stage_id=stage.id,
                )
            )
        for name in collect_placeholders(title, instructions):
            if name not in discovered_seen:
                discovered_seen.add(name)
                discovered.append(name)
        expanded_stages.append(
            stage.model_copy(update={"title": title, "instructions": instructions})
        )

    declared = template.inputs or {}
    for name in declared:
        if name not in discovered_seen:
            issues.append(
                WorkflowCompileIssue(
                    code="unused_input",
                    message=f"declared input {name!r} is not used in any stage field",
                    severity="warning",
                    path=["inputs", name],
                    input_name=name,
                )
            )

    inputs = _merge_inputs(declared, discovered)

    expanded = template.model_copy(
        update={
            "stages": expanded_stages,
            # Snapshot keeps declarations so re-inspection of a run can explain
            # wizard metadata; placeholders themselves remain unresolved.
            "inputs": dict(declared),
        }
    )
    ok = not any(issue.severity == "error" for issue in issues)
    return CompileWorkflowTemplateResult(
        ok=ok,
        expanded_template=expanded,
        inputs=inputs,
        issues=issues,
    )


def apply_input_defaults(
    inputs: list[WorkflowInput],
    args: Mapping[str, str] | None,
) -> dict[str, str]:
    """Merge caller *args* over declared/inferred defaults (args win)."""
    merged: dict[str, str] = {}
    for field in inputs:
        if field.default is not None:
            merged[field.name] = field.default
    if args:
        merged.update({key: str(value) for key, value in args.items()})
    return merged


def required_input_issues(
    inputs: list[WorkflowInput],
    args: Mapping[str, str],
) -> list[WorkflowCompileIssue]:
    """Return errors for required inputs that are missing or blank after merge."""
    issues: list[WorkflowCompileIssue] = []
    for field in inputs:
        if not field.required:
            continue
        value = args.get(field.name)
        if value is None or not str(value).strip():
            issues.append(
                WorkflowCompileIssue(
                    code="required_input_missing",
                    message=f"required input {field.name!r} is not filled",
                    severity="error",
                    path=["inputs", field.name],
                    input_name=field.name,
                )
            )
    return issues


def prompt_template_map(
    records: list[Mapping[str, str]] | None = None,
) -> dict[str, str]:
    """Build a name→body map from ``load_templates()``-shaped records."""
    if records is None:
        from murder.user_config import load_templates  # noqa: PLC0415

        records = load_templates()
    out: dict[str, str] = {}
    for record in records:
        name = str(record.get("name", ""))
        if name:
            out[name] = str(record.get("body", ""))
    return out


def _merge_inputs(
    declared: Mapping[str, WorkflowInputDecl],
    discovered: list[str],
) -> list[WorkflowInput]:
    """Declared inputs first (declaration order), then inferred discoveries."""
    inputs: list[WorkflowInput] = []
    seen: set[str] = set()

    for name, decl in declared.items():
        seen.add(name)
        label = decl.label if decl.label else name
        inputs.append(
            WorkflowInput(
                name=name,
                label=label,
                kind=decl.kind,
                required=decl.required,
                default=decl.default,
                inferred=False,
            )
        )

    for name in discovered:
        if name in seen:
            continue
        seen.add(name)
        inputs.append(
            WorkflowInput(
                name=name,
                label=name,
                kind="text",
                required=False,
                default=None,
                inferred=True,
            )
        )
    return inputs


__all__ = [
    "CompileWorkflowTemplateParams",
    "CompileWorkflowTemplateResult",
    "WorkflowCompileIssue",
    "WorkflowCompileIssueCode",
    "WorkflowInput",
    "apply_input_defaults",
    "collect_placeholders",
    "compile_workflow_template",
    "expand_inline_prompt_templates",
    "prompt_template_map",
    "required_input_issues",
]
