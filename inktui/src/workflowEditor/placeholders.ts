import type { EditorWorkflow } from './model.js';

export const PLACEHOLDER = /\{([A-Za-z0-9_-]+)\}/g;

/** Append distinct `{placeholder}` names from `text` into `names` (first-appearance order). */
export function collectPlaceholdersFromText(
  text: string,
  names: string[],
  seen: Set<string>,
): void {
  for (const match of text.matchAll(PLACEHOLDER)) {
    const name = match[1];
    if (name === undefined) continue;
    if (!seen.has(name)) {
      seen.add(name);
      names.push(name);
    }
  }
}

/** Distinct `{placeholder}` names in stage title/instruction definition order. */
export function collectPlaceholders(workflow: EditorWorkflow): readonly string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const stage of workflow.stages)
    for (const text of [stage.title, stage.instructions])
      collectPlaceholdersFromText(text, names, seen);
  return names;
}
