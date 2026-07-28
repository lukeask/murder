import type { EditorWorkflow } from './model.js';
export const PLACEHOLDER = /\{([A-Za-z0-9_-]+)\}/g;
export function collectPlaceholders(workflow: EditorWorkflow): readonly string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const stage of workflow.stages)
    for (const text of [stage.title, stage.instructions]) {
      for (const match of text.matchAll(PLACEHOLDER)) {
        const name = match[1];
        if (name === undefined) continue;
        if (!seen.has(name)) {
          seen.add(name);
          names.push(name);
        }
      }
    }
  return names;
}
