/**
 * Pure workflow-template library helpers — mirrored from the TUI library mode without importing
 * inktui. Shared by {@link WorkflowTemplateLibrary} and its tests.
 */

import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';

/** Case-insensitive name sort used in both library sections. */
export function sortWorkflowTemplates(
  workflows: readonly WorkflowTemplate[],
): readonly WorkflowTemplate[] {
  return [...workflows].sort((a, b) => a.name.localeCompare(b.name));
}

/** Split the registry without treating a missing `builtin` value as read-only. */
export function partitionWorkflowTemplates(workflows: readonly WorkflowTemplate[]): {
  readonly mine: readonly WorkflowTemplate[];
  readonly builtIn: readonly WorkflowTemplate[];
} {
  return {
    mine: sortWorkflowTemplates(workflows.filter((workflow) => workflow.builtin !== true)),
    builtIn: sortWorkflowTemplates(workflows.filter((workflow) => workflow.builtin === true)),
  };
}

/** Name matching is intentionally forgiving; execution remains exact-name only. */
export function filterWorkflowTemplates(
  workflows: readonly WorkflowTemplate[],
  filter: string,
): readonly WorkflowTemplate[] {
  const query = filter.trim().toLocaleLowerCase();
  if (query.length === 0) return workflows;
  return workflows.filter((workflow) => workflow.name.toLocaleLowerCase().includes(query));
}

/** Copy-name policy shared by library actions and tests. */
export function copiedWorkflowName(oldName: string, existingNames: ReadonlySet<string>): string {
  const base = `Copy of ${oldName}`;
  if (!existingNames.has(base)) return base;
  let number = 2;
  while (existingNames.has(`${base} ${number}`)) number += 1;
  return `${base} ${number}`;
}

/**
 * Detached definition for the editor create flow. `structuredClone` keeps stage/input objects from
 * sharing identity with a saved built-in.
 */
export function copyWorkflowTemplate(
  workflow: WorkflowTemplate,
  existingNames: ReadonlySet<string>,
): WorkflowTemplate {
  const copy = structuredClone(workflow);
  return {
    ...copy,
    name: copiedWorkflowName(workflow.name, existingNames),
    builtin: false,
    definition_version: 1,
  };
}
