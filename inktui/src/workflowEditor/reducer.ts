import { buildEditorGraph, reachable } from './graph.js';
import type { EditableField, EditorStage, EditorWorkflow, StageKey } from './model.js';
import { createLocalStageKey } from './wire.js';

/** Local edit operations.  These deliberately know nothing about React or persistence. */
export type WorkflowEdit =
  | { readonly type: 'replace'; readonly workflow: EditorWorkflow }
  | { readonly type: 'add-stage'; readonly after: StageKey | null }
  | { readonly type: 'delete-stage'; readonly key: StageKey }
  | {
      readonly type: 'set-field';
      readonly key: StageKey | 'workflow';
      readonly field: EditableField;
      readonly value: string;
    }
  | { readonly type: 'rename-stage'; readonly key: StageKey; readonly id: string }
  | { readonly type: 'toggle-dependency'; readonly target: StageKey; readonly source: StageKey }
  | { readonly type: 'move-stage'; readonly key: StageKey; readonly delta: -1 | 1 };

export function nextStageId(stages: readonly EditorStage[]): string {
  const existing = new Set(stages.map((stage) => stage.id));
  let number = 1;
  while (existing.has(`stage-${number}`)) number += 1;
  return `stage-${number}`;
}

export function applyWorkflowEdit(workflow: EditorWorkflow, edit: WorkflowEdit): EditorWorkflow {
  switch (edit.type) {
    case 'replace':
      return edit.workflow;
    case 'add-stage': {
      const stage: EditorStage = {
        key: createLocalStageKey(),
        id: nextStageId(workflow.stages),
        title: '',
        instructions: '',
        harness: null,
        model: null,
        worktree: null,
        dependsOn: [],
        gate: 'auto',
      };
      const index =
        edit.after === null
          ? workflow.stages.length
          : workflow.stages.findIndex((item) => item.key === edit.after) + 1;
      const at = index <= 0 ? workflow.stages.length : index;
      return {
        ...workflow,
        stages: [...workflow.stages.slice(0, at), stage, ...workflow.stages.slice(at)],
      };
    }
    case 'delete-stage': {
      const removed = workflow.stages.find((stage) => stage.key === edit.key);
      if (removed === undefined) return workflow;
      return {
        ...workflow,
        stages: workflow.stages
          .filter((stage) => stage.key !== edit.key)
          .map((stage) => ({
            ...stage,
            dependsOn: stage.dependsOn.filter((dependency) => dependency !== removed.id),
          })),
      };
    }
    case 'set-field': {
      if (edit.key === 'workflow') {
        if (edit.field === 'name' || edit.field === 'description') {
          return { ...workflow, [edit.field]: edit.value };
        }
        if (edit.field === 'mode') {
          return edit.value === 'static' || edit.value === 'generative'
            ? { ...workflow, mode: edit.value }
            : workflow;
        }
        return workflow;
      }
      if (edit.field === 'id') {
        return renameStage(workflow, edit.key, edit.value);
      }
      return {
        ...workflow,
        stages: workflow.stages.map((stage) => {
          if (stage.key !== edit.key) return stage;
          if (edit.field === 'title' || edit.field === 'instructions')
            return { ...stage, [edit.field]: edit.value };
          if (edit.field === 'harness' || edit.field === 'model' || edit.field === 'worktree')
            return { ...stage, [edit.field]: edit.value === '' ? null : edit.value };
          if (edit.field === 'gate')
            return {
              ...stage,
              gate: edit.value === 'human' || edit.value === 'conditional' ? edit.value : 'auto',
            };
          return stage;
        }),
      };
    }
    case 'rename-stage':
      return renameStage(workflow, edit.key, edit.id);
    case 'toggle-dependency':
      return toggleDependency(workflow, edit.target, edit.source);
    case 'move-stage': {
      const from = workflow.stages.findIndex((stage) => stage.key === edit.key);
      const to = from + edit.delta;
      if (from < 0 || to < 0 || to >= workflow.stages.length) return workflow;
      const stages = [...workflow.stages];
      const [stage] = stages.splice(from, 1);
      if (stage === undefined) return workflow;
      stages.splice(to, 0, stage);
      return { ...workflow, stages };
    }
    default:
      return edit satisfies never;
  }
}

/** Rename and reference rewriting are a single atomic document operation. */
export function renameStage(workflow: EditorWorkflow, key: StageKey, id: string): EditorWorkflow {
  const stage = workflow.stages.find((item) => item.key === key);
  if (stage === undefined || stage.id === id) return workflow;
  const matching = workflow.stages.filter((item) => item.id === stage.id);
  // Rewriting a duplicate ID would silently choose one of several possible targets.
  if (matching.length !== 1) return workflow;
  return {
    ...workflow,
    stages: workflow.stages.map((item) =>
      item.key === key
        ? { ...item, id }
        : {
            ...item,
            dependsOn: item.dependsOn.map((dependency) =>
              dependency === stage.id ? id : dependency,
            ),
          },
    ),
  };
}

/** Add/remove source -> target. Invalid/ambiguous/cyclic additions are no-ops. */
export function toggleDependency(
  workflow: EditorWorkflow,
  target: StageKey,
  source: StageKey,
): EditorWorkflow {
  const targetStage = workflow.stages.find((stage) => stage.key === target);
  const sourceStage = workflow.stages.find((stage) => stage.key === source);
  if (targetStage === undefined || sourceStage === undefined) return workflow;
  const existingCount = targetStage.dependsOn.filter((id) => id === sourceStage.id).length;
  if (existingCount === 1) {
    return {
      ...workflow,
      stages: workflow.stages.map((stage) =>
        stage.key === target
          ? { ...stage, dependsOn: stage.dependsOn.filter((id) => id !== sourceStage.id) }
          : stage,
      ),
    };
  }
  if (existingCount > 1) return workflow;
  const idOccurrences = workflow.stages.filter((stage) => stage.id === sourceStage.id).length;
  if (
    source === target ||
    sourceStage.id === '' ||
    idOccurrences !== 1 ||
    reachable(buildEditorGraph(workflow), target, source)
  )
    return workflow;
  return {
    ...workflow,
    stages: workflow.stages.map((stage) =>
      stage.key === target ? { ...stage, dependsOn: [...stage.dependsOn, sourceStage.id] } : stage,
    ),
  };
}

/** Snapshot history deliberately sits above individual edit operations: a field transaction is one edit. */
export interface HistoryEntry {
  readonly draft: EditorWorkflow;
  readonly selected: StageKey | null;
}
export interface EditorState {
  readonly base: EditorWorkflow;
  readonly draft: EditorWorkflow;
  readonly selected: StageKey | null;
  readonly undo: readonly HistoryEntry[];
  readonly redo: readonly HistoryEntry[];
}
export const HISTORY_LIMIT = 100;
export type HistoryAction =
  | { readonly type: 'undo' }
  | { readonly type: 'redo' }
  | { readonly type: 'saved'; readonly workflow: EditorWorkflow }
  | { readonly type: 'edit'; readonly edit: WorkflowEdit; readonly selected?: StageKey | null };

export function initialEditorState(workflow: EditorWorkflow): EditorState {
  return {
    base: workflow,
    draft: workflow,
    selected: workflow.stages[0]?.key ?? null,
    undo: [],
    redo: [],
  };
}

export function reduceEditor(state: EditorState, action: HistoryAction): EditorState {
  if (action.type === 'undo') {
    const entry = state.undo.at(-1);
    return entry === undefined
      ? state
      : {
          ...state,
          draft: entry.draft,
          selected: entry.selected,
          undo: state.undo.slice(0, -1),
          redo: [...state.redo, { draft: state.draft, selected: state.selected }],
        };
  }
  if (action.type === 'redo') {
    const entry = state.redo.at(-1);
    return entry === undefined
      ? state
      : {
          ...state,
          draft: entry.draft,
          selected: entry.selected,
          redo: state.redo.slice(0, -1),
          undo: [...state.undo, { draft: state.draft, selected: state.selected }],
        };
  }
  if (action.type === 'saved') return { ...state, base: action.workflow };
  const draft = applyWorkflowEdit(state.draft, action.edit);
  if (draft === state.draft) return state;
  return {
    ...state,
    draft,
    selected: action.selected === undefined ? state.selected : action.selected,
    undo: [...state.undo, { draft: state.draft, selected: state.selected }].slice(-HISTORY_LIMIT),
    redo: [],
  };
}

export function dependencyLegality(
  workflow: EditorWorkflow,
  target: StageKey,
  candidate: StageKey,
): 'add' | 'remove' | 'cycle' | 'invalid' {
  const targetStage = workflow.stages.find((stage) => stage.key === target);
  const candidateStage = workflow.stages.find((stage) => stage.key === candidate);
  if (
    targetStage === undefined ||
    candidateStage === undefined ||
    target === candidate ||
    candidateStage.id === '' ||
    workflow.stages.filter((stage) => stage.id === candidateStage.id).length !== 1
  )
    return 'invalid';
  const count = targetStage.dependsOn.filter((id) => id === candidateStage.id).length;
  if (count === 1) return 'remove';
  if (count > 1) return 'invalid';
  return reachable(buildEditorGraph(workflow), target, candidate) ? 'cycle' : 'add';
}
