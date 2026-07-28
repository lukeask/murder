import type { QueryResult } from '../generated/applicationProtocol.js';
import type { EditorStage, EditorWorkflow, StageGate, StageKey } from './model.js';

export type WorkflowWire = QueryResult<'workflows.get'>['workflows'][number];
export type StageWire = NonNullable<WorkflowWire['stages']>[number];

let keySequence = 0;
export function createLocalStageKey(): StageKey {
  keySequence += 1;
  return `stage-${keySequence}`;
}

function gate(value: StageWire['gate']): StageGate {
  return value === 'human' || value === 'conditional' ? value : 'auto';
}

export function fromWire(workflow: WorkflowWire): EditorWorkflow {
  return {
    name: workflow.name,
    ...(workflow.definition_version === undefined
      ? {}
      : { definitionVersion: workflow.definition_version }),
    description: workflow.description ?? '',
    mode: workflow.mode ?? 'static',
    stages: (workflow.stages ?? []).map(
      (stage): EditorStage => ({
        key: createLocalStageKey(),
        id: stage.id,
        title: stage.title,
        instructions: stage.instructions ?? '',
        harness: stage.harness ?? null,
        model: stage.model ?? null,
        worktree: stage.worktree ?? null,
        dependsOn: stage.depends_on ?? [],
        gate: gate(stage.gate),
      }),
    ),
  };
}

export function toWire(workflow: EditorWorkflow): WorkflowWire {
  return {
    name: workflow.name,
    ...(workflow.definitionVersion === undefined
      ? {}
      : { definition_version: workflow.definitionVersion }),
    description: workflow.description,
    mode: workflow.mode,
    stages: workflow.stages.map((stage) => ({
      id: stage.id,
      title: stage.title,
      instructions: stage.instructions,
      harness: stage.harness,
      model: stage.model,
      worktree: stage.worktree,
      depends_on: stage.dependsOn,
      gate: stage.gate,
    })),
  };
}
