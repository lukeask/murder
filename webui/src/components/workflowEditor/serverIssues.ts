/**
 * Map `workflow.put` rejection issues onto EditorIssue (same path rules as the TUI editor).
 */

import type { EditorIssue, EditorWorkflow } from '@murder/ui-core/workflowEditor/model.js';

export function editorIssueFromServer(
  draft: EditorWorkflow,
  issue: {
    readonly code: EditorIssue['code'];
    readonly message: string;
    readonly path?: readonly (string | number)[];
    readonly stage_id?: string | null;
    readonly severity?: 'error' | 'warning';
  },
): EditorIssue {
  const stageIndex =
    issue.path?.[0] === 'stages' && typeof issue.path[1] === 'number' ? issue.path[1] : undefined;
  const uniqueIdMatches =
    issue.stage_id == null ? [] : draft.stages.filter((stage) => stage.id === issue.stage_id);
  const stageKey =
    stageIndex === undefined
      ? uniqueIdMatches.length === 1
        ? uniqueIdMatches[0]?.key
        : undefined
      : draft.stages[stageIndex]?.key;
  const dependencyIndex =
    issue.path?.[2] === 'depends_on' && typeof issue.path[3] === 'number'
      ? issue.path[3]
      : undefined;
  const lastPath = issue.path?.at(-1);
  const field =
    typeof lastPath === 'string'
      ? (
          {
            depends_on: undefined,
            name: 'name',
            description: 'description',
            mode: 'mode',
            id: 'id',
            title: 'title',
            instructions: 'instructions',
            harness: 'harness',
            model: 'model',
            worktree: 'worktree',
            gate: 'gate',
          } as const
        )[lastPath as 'name' | 'description' | 'mode' | 'id' | 'title' | 'instructions' | 'harness' | 'model' | 'worktree' | 'gate' | 'depends_on']
      : undefined;
  return {
    code: issue.code,
    severity: issue.severity ?? 'error',
    message: issue.message,
    ...(stageKey === undefined ? {} : { stageKey }),
    ...(dependencyIndex === undefined ? {} : { dependencyIndex }),
    ...(field === undefined ? {} : { field }),
  };
}
