/**
 * Pure analysis helpers for prompt-template bodies and their references from workflow templates.
 * Used by {@link ../PromptTemplateManagerMode.js} for preview / rename-delete guards.
 */

import type { WorkflowTemplate } from '../../store/workflows/workflowsSlice.js';

/** Inline `:name:` macros inside a body (or workflow field). */
export const INLINE_TEMPLATE_RE = /:([A-Za-z0-9_-]+):/g;
/** `{placeholder}` tokens inside a template body. */
export const BODY_PLACEHOLDER_RE = /\{([A-Za-z0-9_-]+)\}/g;
/** Valid prompt-template name. */
export const TEMPLATE_NAME_RE = /^[A-Za-z0-9_-]+$/;

/** One site where a workflow template textually references `:name:`. */
export interface WorkflowTemplateRef {
  readonly workflowName: string;
  readonly stageId: string;
  readonly field: 'title' | 'instructions';
}

/** Compact `workflow/stage.field` label for referential warnings. */
export function formatWorkflowTemplateRef(ref: WorkflowTemplateRef): string {
  return `${ref.workflowName}/${ref.stageId}.${ref.field}`;
}

/** Distinct `{placeholder}` names in first-appearance order. */
export function collectBodyPlaceholders(body: string): readonly string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const match of body.matchAll(BODY_PLACEHOLDER_RE)) {
    const name = match[1];
    if (name === undefined || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }
  return names;
}

/** Distinct inline `:name:` references in first-appearance order. */
export function collectInlineTemplateRefs(body: string): readonly string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const match of body.matchAll(INLINE_TEMPLATE_RE)) {
    const name = match[1];
    if (name === undefined || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }
  return names;
}

/** Inline refs whose names are not present in `knownNames` (excluding optional `selfName`). */
export function collectUnknownInlineRefs(
  body: string,
  knownNames: ReadonlySet<string>,
  selfName?: string,
): readonly string[] {
  return collectInlineTemplateRefs(body).filter(
    (name) => name !== selfName && !knownNames.has(name),
  );
}

/** Single-pass inline `:name:` expansion for preview (unknowns left verbatim). */
export function expandInlinePreview(
  text: string,
  templates: ReadonlyMap<string, string>,
): { text: string; missing: readonly string[] } {
  const missing: string[] = [];
  const seen = new Set<string>();
  const result = text.replace(INLINE_TEMPLATE_RE, (whole, name: string) => {
    const body = templates.get(name);
    if (body === undefined) {
      if (!seen.has(name)) {
        seen.add(name);
        missing.push(name);
      }
      return whole;
    }
    return body;
  });
  return { text: result, missing };
}

/** Find workflow-template fields that contain `:templateName:` as an inline ref. */
export function findWorkflowReferences(
  templateName: string,
  workflows: readonly WorkflowTemplate[],
): readonly WorkflowTemplateRef[] {
  const needle = `:${templateName}:`;
  const refs: WorkflowTemplateRef[] = [];
  for (const workflow of workflows) {
    for (const stage of workflow.stages ?? []) {
      if (stage.title.includes(needle)) {
        refs.push({ workflowName: workflow.name, stageId: stage.id, field: 'title' });
      }
      if ((stage.instructions ?? '').includes(needle)) {
        refs.push({ workflowName: workflow.name, stageId: stage.id, field: 'instructions' });
      }
    }
  }
  return refs;
}

/** Flatten a body into a single preview line (newlines → `⏎`, trimmed, capped). */
export function previewBodyFlat(body: string, maxLen = 200): string {
  const flat = body.replace(/\s*\n\s*/g, ' ⏎ ').trim();
  if (flat === '') return '(empty)';
  return flat.length > maxLen ? `${flat.slice(0, maxLen)}…` : flat;
}

/** Validate a proposed template name; `originalName` allows keeping the same name on rename. */
export function validateTemplateName(
  name: string,
  originalName: string | null,
  existing: readonly { readonly name: string }[],
): string | null {
  if (name === '') return 'Template name cannot be empty';
  if (!TEMPLATE_NAME_RE.test(name)) {
    return `"${name}" is invalid — use letters, digits, _ or - only`;
  }
  if (name !== originalName && existing.some((t) => t.name === name)) {
    return `A template named "${name}" already exists`;
  }
  return null;
}
