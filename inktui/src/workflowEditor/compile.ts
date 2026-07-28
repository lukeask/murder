/**
 * Workflow-template compilation for the editor run wizard.
 *
 * Order (mirrors the overhaul plan / server `compile_workflow_template`):
 *   source title/instructions → expand inline `:foo:` → collect `{placeholders}` → wizard fields.
 *
 * Prefer the `workflow.compile` RPC (via workflows actions) for preview/wizard generation.
 * {@link compileWorkflowTemplate} is the offline / RPC-error fallback; `workflow.start` still
 * compiles authoritatively on the server before creating a run.
 */

import type { QueryResult } from '../generated/applicationProtocol.js';
import { expandInlinePromptTemplates } from '../input/expandTemplates.js';
import type { EditorInputDecl, EditorWorkflow, StageKey } from './model.js';
import { collectPlaceholdersFromText } from './placeholders.js';

/** Optional declared input metadata (also lives on {@link EditorWorkflow.inputs}). */
export type WorkflowInputDecl = EditorInputDecl;

/** One generated wizard field (one per distinct placeholder after expansion). */
export type WizardField = {
  readonly name: string;
  readonly label: string;
  readonly kind: 'text' | 'multiline';
  readonly required: boolean;
  readonly defaultValue: string;
};

export type WorkflowCompileIssue = {
  readonly code: 'unknown_prompt_template' | 'unused_input' | 'required_input_missing';
  readonly severity: 'error' | 'warning';
  readonly message: string;
  readonly stageKey?: StageKey;
  /** Wire stage id from `workflow.compile` (map to a local {@link stageKey} in the editor). */
  readonly stageId?: string;
  readonly field?: 'title' | 'instructions';
  readonly templateName?: string;
  readonly inputName?: string;
};

export type WorkflowCompileResult = {
  /** Expanded stage texts (title/instructions) after single-pass `:name:` expansion. */
  readonly expanded: EditorWorkflow;
  /** Wizard fields in first-occurrence order across stages (title then instructions). */
  readonly fields: readonly WizardField[];
  /** Distinct placeholder names (same order as {@link fields}). */
  readonly placeholders: readonly string[];
  /** Blocking diagnostics — e.g. unknown `:template:` refs. */
  readonly issues: readonly WorkflowCompileIssue[];
};

export type WorkflowCompileRpcResult = QueryResult<'workflow.compile'>;

function declaredInputsOf(
  workflow: EditorWorkflow,
): Readonly<Record<string, WorkflowInputDecl>> | undefined {
  return workflow.inputs;
}

function fieldFor(
  name: string,
  declared: Readonly<Record<string, WorkflowInputDecl>> | undefined,
): WizardField {
  const decl = declared?.[name];
  return {
    name,
    label: decl?.label?.trim() || name,
    kind: decl?.kind === 'multiline' ? 'multiline' : 'text',
    required: decl?.required ?? false,
    defaultValue: decl?.default ?? '',
  };
}

/**
 * Merge declared inputs (declaration order) with placeholders discovered after expansion —
 * mirrors server `_merge_inputs`.
 */
function mergeWizardFields(
  declared: Readonly<Record<string, WorkflowInputDecl>> | undefined,
  discovered: readonly string[],
): WizardField[] {
  const fields: WizardField[] = [];
  const seen = new Set<string>();
  if (declared !== undefined) {
    for (const name of Object.keys(declared)) {
      seen.add(name);
      fields.push(fieldFor(name, declared));
    }
  }
  for (const name of discovered) {
    if (seen.has(name)) continue;
    seen.add(name);
    fields.push(fieldFor(name, declared));
  }
  return fields;
}

/** Map a `workflow.compile` RPC result into editor wizard fields + diagnostics. */
export function wizardFieldsFromCompileResult(
  result: WorkflowCompileRpcResult,
): {
  readonly fields: readonly WizardField[];
  readonly issues: readonly WorkflowCompileIssue[];
  readonly ok: boolean;
} {
  const fields = (result.inputs ?? []).map(
    (input): WizardField => ({
      name: input.name,
      label: input.label.trim() || input.name,
      kind: input.kind === 'multiline' ? 'multiline' : 'text',
      required: input.required ?? false,
      defaultValue: input.default ?? '',
    }),
  );
  const issues = (result.issues ?? []).map(
    (issue): WorkflowCompileIssue => ({
      code: issue.code,
      severity: issue.severity ?? 'error',
      message: issue.message,
      ...(issue.template_name == null ? {} : { templateName: issue.template_name }),
      ...(issue.input_name == null ? {} : { inputName: issue.input_name }),
      ...(issue.stage_id == null ? {} : { stageId: issue.stage_id }),
    }),
  );
  return { fields, issues, ok: result.ok };
}

/**
 * Mirror server `required_input_issues`: errors for required wizard fields that are missing
 * or blank after defaults/args merge.
 */
export function requiredInputIssues(
  fields: readonly WizardField[],
  args: Readonly<Record<string, string>>,
): readonly WorkflowCompileIssue[] {
  const issues: WorkflowCompileIssue[] = [];
  for (const field of fields) {
    if (!field.required) continue;
    const value = args[field.name];
    if (value === undefined || !value.trim()) {
      issues.push({
        code: 'required_input_missing',
        severity: 'error',
        message: `required input '${field.name}' is not filled`,
        inputName: field.name,
      });
    }
  }
  return issues;
}

/**
 * Compile a workflow template draft for the run wizard: expand inline prompt templates in each
 * stage's title/instructions, then collect `{placeholder}` tokens from the expanded text.
 * Offline / RPC-error fallback — prefer `workflow.compile` when the application client is available.
 */
export function compileWorkflowTemplate(
  workflow: EditorWorkflow,
  templates: ReadonlyMap<string, string>,
  options: {
    readonly declaredInputs?: Readonly<Record<string, WorkflowInputDecl>>;
  } = {},
): WorkflowCompileResult {
  const declared = options.declaredInputs ?? declaredInputsOf(workflow);
  const issues: WorkflowCompileIssue[] = [];
  const names: string[] = [];
  const seen = new Set<string>();

  const expandedStages = workflow.stages.map((stage) => {
    const titleResult = expandInlinePromptTemplates(stage.title, templates);
    const instructionsResult = expandInlinePromptTemplates(stage.instructions, templates);

    for (const templateName of titleResult.missing) {
      issues.push({
        code: 'unknown_prompt_template',
        severity: 'error',
        message: `Unknown prompt template :${templateName}: in stage “${stage.id || stage.key}” title.`,
        stageKey: stage.key,
        field: 'title',
        templateName,
      });
    }
    for (const templateName of instructionsResult.missing) {
      issues.push({
        code: 'unknown_prompt_template',
        severity: 'error',
        message: `Unknown prompt template :${templateName}: in stage “${stage.id || stage.key}” instructions.`,
        stageKey: stage.key,
        field: 'instructions',
        templateName,
      });
    }

    collectPlaceholdersFromText(titleResult.text, names, seen);
    collectPlaceholdersFromText(instructionsResult.text, names, seen);

    return {
      ...stage,
      title: titleResult.text,
      instructions: instructionsResult.text,
    };
  });

  if (declared !== undefined) {
    for (const name of Object.keys(declared)) {
      if (!seen.has(name)) {
        issues.push({
          code: 'unused_input',
          severity: 'warning',
          message: `declared input '${name}' is not used in any stage field`,
          inputName: name,
        });
      }
    }
  }

  const fields = mergeWizardFields(declared, names);

  return {
    expanded: { ...workflow, stages: expandedStages },
    fields,
    placeholders: fields.map((field) => field.name),
    issues,
  };
}
