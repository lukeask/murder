/**
 * Pure analysis helpers for prompt-template bodies and their references from workflow templates.
 * Used by {@link ../PromptTemplateManagerMode.js} for preview / rename-delete guards.
 */

import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';

/**
 * Inline prompt-template macros inside a body (or workflow field). Legacy `:name:` refs keep
 * their identifier-only grammar so times and versions remain literal; quoted refs support the
 * human-readable names accepted by the registry: `:"Name With Spaces":`.
 */
export const INLINE_PROMPT_TEMPLATE_RE = /:"([^"\r\n:]+)":|:([A-Za-z_][A-Za-z0-9_-]*):/g;
/** `{placeholder}` tokens inside a template body. */
export const BODY_PLACEHOLDER_RE = /\{([A-Za-z0-9_-]+)\}/g;
/** Valid human-readable prompt-template name. Whitespace is only the single space separator. */
export const PROMPT_TEMPLATE_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*(?: [A-Za-z0-9][A-Za-z0-9_-]*)*$/;

/** Backwards-compatible aliases for transitional callers. New code uses the prompt-template names. */
export const INLINE_TEMPLATE_RE = INLINE_PROMPT_TEMPLATE_RE;
export const TEMPLATE_NAME_RE = PROMPT_TEMPLATE_NAME_RE;

/** Render a prompt-template macro, quoting names that cannot use the legacy bare form. */
export function formatPromptTemplateMacro(name: string): string {
  return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(name) ? `:${name}:` : `:"${name}":`;
}

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

/** Distinct inline prompt-template references in first-appearance order. */
export function collectInlinePromptTemplateRefs(body: string): readonly string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const match of body.matchAll(INLINE_PROMPT_TEMPLATE_RE)) {
    const name = match[1] ?? match[2];
    if (name === undefined || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }
  return names;
}

/** @deprecated Use {@link collectInlinePromptTemplateRefs}. */
export const collectInlineTemplateRefs = collectInlinePromptTemplateRefs;

/** Inline refs whose names are not present in `knownNames` (excluding optional `selfName`). */
export function collectUnknownInlinePromptTemplateRefs(
  body: string,
  knownNames: ReadonlySet<string>,
  selfName?: string,
): readonly string[] {
  return collectInlinePromptTemplateRefs(body).filter(
    (name) => name !== selfName && !knownNames.has(name),
  );
}

/** @deprecated Use {@link collectUnknownInlinePromptTemplateRefs}. */
export const collectUnknownInlineRefs = collectUnknownInlinePromptTemplateRefs;

/** Single-pass inline `:name:` expansion for preview (unknowns left verbatim). */
export function expandInlinePromptTemplatePreview(
  text: string,
  templates: ReadonlyMap<string, string>,
): { text: string; missing: readonly string[] } {
  const missing: string[] = [];
  const seen = new Set<string>();
  const result = text.replace(
    INLINE_PROMPT_TEMPLATE_RE,
    (whole, quotedName: string | undefined, bareName: string | undefined) => {
      const name = quotedName ?? bareName;
      if (name === undefined) return whole;
      const body = templates.get(name);
      if (body === undefined) {
        if (!seen.has(name)) {
          seen.add(name);
          missing.push(name);
        }
        return whole;
      }
      return body;
    },
  );
  return { text: result, missing };
}

/** @deprecated Use {@link expandInlinePromptTemplatePreview}. */
export const expandInlinePreview = expandInlinePromptTemplatePreview;

/** Find workflow fields that contain this prompt-template as an inline ref. */
export function findWorkflowPromptTemplateReferences(
  templateName: string,
  workflows: readonly WorkflowTemplate[],
): readonly WorkflowTemplateRef[] {
  const refsIn = (text: string): boolean =>
    collectInlinePromptTemplateRefs(text).some((name) => name === templateName);
  const refs: WorkflowTemplateRef[] = [];
  for (const workflow of workflows) {
    for (const stage of workflow.stages ?? []) {
      if (refsIn(stage.title)) {
        refs.push({ workflowName: workflow.name, stageId: stage.id, field: 'title' });
      }
      if (refsIn(stage.instructions ?? '')) {
        refs.push({ workflowName: workflow.name, stageId: stage.id, field: 'instructions' });
      }
    }
  }
  return refs;
}

/** @deprecated Use {@link findWorkflowPromptTemplateReferences}. */
export const findWorkflowReferences = findWorkflowPromptTemplateReferences;

/** Flatten a body into a single preview line (newlines → `⏎`, trimmed, capped). */
export function previewBodyFlat(body: string, maxLen = 200): string {
  const flat = body.replace(/\s*\n\s*/g, ' ⏎ ').trim();
  if (flat === '') return '(empty)';
  return flat.length > maxLen ? `${flat.slice(0, maxLen)}…` : flat;
}

/** Validate a proposed template name; `originalName` allows keeping the same name on rename. */
export function validatePromptTemplateName(
  name: string,
  originalName: string | null,
  existing: readonly { readonly name: string }[],
): string | null {
  if (name === '') return 'Prompt template name cannot be empty';
  if (!PROMPT_TEMPLATE_NAME_RE.test(name)) {
    return `"${name}" is invalid — use alphanumeric words separated by single spaces; _ and - are allowed`;
  }
  if (name !== originalName && existing.some((t) => t.name === name)) {
    return `A prompt template named "${name}" already exists`;
  }
  return null;
}

/** @deprecated Use {@link validatePromptTemplateName}. */
export const validateTemplateName = validatePromptTemplateName;
