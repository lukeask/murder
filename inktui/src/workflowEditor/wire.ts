import type { QueryResult } from '../generated/applicationProtocol.js';
import type { EditorInputDecl, EditorStage, EditorWorkflow, StageGate, StageKey } from './model.js';

export type WorkflowWire = QueryResult<'workflows.get'>['workflows'][number];
export type StageWire = NonNullable<WorkflowWire['stages']>[number];
export type InputDeclWire = NonNullable<WorkflowWire['inputs']>[string];

let keySequence = 0;
export function createLocalStageKey(): StageKey {
  keySequence += 1;
  return `stage-${keySequence}`;
}

function gate(value: StageWire['gate']): StageGate {
  return value === 'human' || value === 'conditional' ? value : 'auto';
}

function inputsFromWire(
  inputs: WorkflowWire['inputs'],
): Readonly<Record<string, EditorInputDecl>> | undefined {
  if (inputs === undefined) return undefined;
  const out: Record<string, EditorInputDecl> = {};
  for (const [name, decl] of Object.entries(inputs)) {
    out[name] = {
      ...(decl.label == null ? {} : { label: decl.label }),
      ...(decl.kind === 'multiline' || decl.kind === 'text' ? { kind: decl.kind } : {}),
      ...(decl.required === undefined ? {} : { required: decl.required }),
      ...(decl.default == null ? {} : { default: decl.default }),
    };
  }
  return out;
}

function inputsToWire(inputs: EditorWorkflow['inputs']): WorkflowWire['inputs'] | undefined {
  if (inputs === undefined) return undefined;
  const out: Record<string, InputDeclWire> = {};
  for (const [name, decl] of Object.entries(inputs)) {
    out[name] = {
      ...(decl.label === undefined ? {} : { label: decl.label }),
      ...(decl.kind === undefined ? {} : { kind: decl.kind }),
      ...(decl.required === undefined ? {} : { required: decl.required }),
      ...(decl.default === undefined ? {} : { default: decl.default }),
    };
  }
  return out;
}

export function fromWire(workflow: WorkflowWire): EditorWorkflow {
  const inputs = inputsFromWire(workflow.inputs);
  return {
    name: workflow.name,
    ...(workflow.definition_version === undefined
      ? {}
      : { definitionVersion: workflow.definition_version }),
    description: workflow.description ?? '',
    mode: workflow.mode ?? 'static',
    ...(inputs === undefined ? {} : { inputs }),
    stages: (workflow.stages ?? []).map(
      (stage): EditorStage => ({
        key: createLocalStageKey(),
        id: stage.id,
        // A stage has one user-facing name.  Keep the wire title synchronized
        // for older registries and the ticket title materialization path.
        title: stage.id,
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
  const inputs = inputsToWire(workflow.inputs);
  return {
    name: workflow.name,
    ...(workflow.definitionVersion === undefined
      ? {}
      : { definition_version: workflow.definitionVersion }),
    description: workflow.description,
    mode: workflow.mode,
    ...(inputs === undefined ? {} : { inputs }),
    stages: workflow.stages.map((stage) => ({
      id: stage.id,
      title: stage.id,
      instructions: stage.instructions,
      harness: stage.harness,
      model: stage.model,
      worktree: stage.worktree,
      depends_on: stage.dependsOn,
      gate: stage.gate,
    })),
  };
}
