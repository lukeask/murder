/**
 * Client-side workflow-template compilation for the editor wizard.
 *
 * Order (mirrors the overhaul plan):
 *   source title/instructions → expand inline `:foo:` → collect `{placeholders}` → wizard fields.
 *
 * Authoritative compile at `workflow.start` is not in the generated protocol yet; this path is for
 * preview / wizard generation / unknown-template diagnostics. Start still goes through the existing
 * start RPC; the server snapshots the saved definition so later template edits cannot rewrite a run.
 */

import { expandInlinePromptTemplates } from '../input/expandTemplates.js';
import type { EditorWorkflow, StageKey } from './model.js';
import { collectPlaceholdersFromText } from './placeholders.js';

/** Optional declared input metadata (forward-compatible; wire schema may not expose `inputs` yet). */
export type WorkflowInputDecl = {
  readonly label?: string;
  readonly kind?: 'text' | 'multiline';
  readonly required?: boolean;
  readonly default?: string;
};

/** One generated wizard field (one per distinct placeholder after expansion). */
export type WizardField = {
  readonly name: string;
  readonly label: string;
  readonly kind: 'text' | 'multiline';
  readonly required: boolean;
  readonly defaultValue: string;
};

export type WorkflowCompileIssue = {
  readonly code: 'unknown_prompt_template';
  readonly severity: 'error';
  readonly message: string;
  readonly stageKey?: StageKey;
  readonly field?: 'title' | 'instructions';
  readonly templateName: string;
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

function declaredInputsOf(
  workflow: EditorWorkflow,
): Readonly<Record<string, WorkflowInputDecl>> | undefined {
  const raw = (workflow as EditorWorkflow & { readonly inputs?: unknown }).inputs;
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
  return raw as Readonly<Record<string, WorkflowInputDecl>>;
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
 * Compile a workflow template draft for the run wizard: expand inline prompt templates in each
 * stage's title/instructions, then collect `{placeholder}` tokens from the expanded text.
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

  return {
    expanded: { ...workflow, stages: expandedStages },
    fields: names.map((name) => fieldFor(name, declared)),
    placeholders: names,
    issues,
  };
}
