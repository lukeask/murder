/** Framework-free document model for the workflow template editor. Local keys intentionally survive invalid IDs. */
export type StageKey = string;

export type StageGate = 'auto' | 'human' | 'conditional';
export type WorkflowMode = 'static' | 'generative';

/** Optional author-declared metadata for a `{placeholder}` workflow input. */
export type EditorInputDecl = {
  readonly label?: string;
  readonly kind?: 'text' | 'multiline';
  readonly required?: boolean;
  readonly default?: string;
};

export interface EditorStage {
  readonly key: StageKey;
  readonly id: string;
  readonly title: string;
  readonly instructions: string;
  readonly harness: string | null;
  readonly model: string | null;
  readonly worktree: string | null;
  readonly dependsOn: readonly string[];
  readonly gate: StageGate;
}

export interface EditorWorkflow {
  readonly name: string;
  /** Server-owned version is retained through editor round-trips even though the editor never edits it. */
  readonly definitionVersion?: number;
  readonly description: string;
  readonly mode: WorkflowMode;
  /** Declared input metadata; undeclared `{placeholders}` are still inferred at compile time. */
  readonly inputs?: Readonly<Record<string, EditorInputDecl>>;
  readonly stages: readonly EditorStage[];
}

export type EditableField =
  | 'name'
  | 'description'
  | 'mode'
  | 'id'
  | 'title'
  | 'instructions'
  | 'harness'
  | 'model'
  | 'worktree'
  | 'gate';

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface Viewport {
  readonly x: number;
  readonly y: number;
}

export interface EditorIssue {
  readonly code:
    | 'invalid_name'
    | 'no_stages'
    | 'invalid_stage_id'
    | 'duplicate_stage_id'
    | 'missing_harness'
    | 'missing_model'
    | 'self_dependency'
    | 'unknown_dependency'
    | 'duplicate_dependency'
    | 'ambiguous_dependency'
    | 'cycle'
    | 'no_root'
    | 'unsupported_mode'
    | 'unsupported_gate';
  readonly severity: 'error' | 'warning';
  readonly message: string;
  readonly stageKey?: StageKey;
  readonly dependencyIndex?: number;
  readonly field?: EditableField;
}

export function workflowEqual(a: EditorWorkflow, b: EditorWorkflow): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
