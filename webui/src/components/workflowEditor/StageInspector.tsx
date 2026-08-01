/**
 * Docked stage / workflow inspector — same fields as the TUI stage panel, DOM form controls.
 */

import { HARNESS_ORDER } from '@murder/ui-core/components/spawnWizardMachine.js';
import {
  modelsFor,
  type HarnessModel,
} from '@murder/ui-core/store/dialogs/harnessModelsActions.js';
import {
  MAIN_WORKTREE_KEY,
  NEW_WORKTREE_KEY,
  type WorktreeOption,
} from '@murder/ui-core/store/dialogs/worktreeOptionsActions.js';
import type {
  EditableField,
  EditorIssue,
  EditorStage,
  EditorWorkflow,
  StageKey,
} from '@murder/ui-core/workflowEditor/model.js';
import { Button, Input, Select } from '../ds/index.js';

export type StageInspectorProps = {
  readonly draft: EditorWorkflow;
  readonly selected: StageKey | null;
  readonly issues: readonly EditorIssue[];
  readonly harnessModels: Readonly<Record<string, readonly HarnessModel[]>>;
  readonly worktrees: readonly WorktreeOption[];
  readonly onWorkflowField: (field: 'name' | 'description' | 'mode', value: string) => void;
  readonly onStageField: (key: StageKey, field: EditableField, value: string) => void;
  readonly onDeleteStage: (key: StageKey) => void;
  readonly onAddStage: () => void;
};

function stageIssues(issues: readonly EditorIssue[], key: StageKey): readonly EditorIssue[] {
  return issues.filter((issue) => issue.stageKey === key);
}

function workflowIssues(issues: readonly EditorIssue[]): readonly EditorIssue[] {
  return issues.filter((issue) => issue.stageKey === undefined);
}

function harnessOptions(
  map: Readonly<Record<string, readonly HarnessModel[]>>,
): Array<{ value: string; label: string }> {
  const known = new Set(Object.keys(map));
  const ordered = HARNESS_ORDER.filter((h) => known.has(h));
  const extras = [...known]
    .filter((h) => !(HARNESS_ORDER as readonly string[]).includes(h))
    .sort();
  const list = ordered.length > 0 || extras.length > 0 ? [...ordered, ...extras] : [...HARNESS_ORDER];
  return list.map((h) => ({ value: h, label: h.replace(/_/g, '-') }));
}

function worktreeSelectOptions(
  worktrees: readonly WorktreeOption[],
): Array<{ value: string; label: string }> {
  const opts = worktrees
    .filter((w) => w.key !== MAIN_WORKTREE_KEY && w.key !== NEW_WORKTREE_KEY)
    .map((w) => ({ value: w.key, label: w.label }));
  return [{ value: '', label: '(none)' }, ...opts];
}

function fieldInvalid(
  issues: readonly EditorIssue[],
  field: EditableField | undefined,
): boolean {
  if (field === undefined) return issues.some((i) => i.severity === 'error');
  return issues.some((i) => i.field === field && i.severity === 'error');
}

export function StageInspector({
  draft,
  selected,
  issues,
  harnessModels,
  worktrees,
  onWorkflowField,
  onStageField,
  onDeleteStage,
  onAddStage,
}: StageInspectorProps): React.JSX.Element {
  const stage: EditorStage | null =
    selected === null ? null : (draft.stages.find((s) => s.key === selected) ?? null);
  const stageIssueList = stage === null ? [] : stageIssues(issues, stage.key);
  const wfIssues = workflowIssues(issues);
  const harnessOpts = harnessOptions(harnessModels);
  const modelOpts =
    stage === null
      ? []
      : modelsFor(stage.harness ?? '', harnessModels).map((m) => ({
          value: m.id,
          label: m.label,
        }));

  return (
    <aside className="wfe-inspector" data-testid="wfe-inspector">
      <div className="wfe-inspector__section">
        <h2 className="wfe-inspector__heading">Workflow</h2>
        <Input
          label="Name"
          value={draft.name}
          onChange={(e) => onWorkflowField('name', e.target.value)}
          invalid={fieldInvalid(wfIssues, 'name')}
          {...(() => {
            const hint = wfIssues.find((i) => i.field === 'name')?.message;
            return hint !== undefined ? { hint } : {};
          })()}
        />
        <Input
          label="Description"
          multiline
          rows={2}
          value={draft.description}
          onChange={(e) => onWorkflowField('description', e.target.value)}
        />
        <Select
          label="Mode"
          value={draft.mode}
          options={[
            { value: 'static', label: 'static' },
            { value: 'generative', label: 'generative' },
          ]}
          onChange={(e) => onWorkflowField('mode', e.target.value)}
        />
      </div>

      <div className="wfe-inspector__section">
        <div className="wfe-inspector__row">
          <h2 className="wfe-inspector__heading">Stage</h2>
          <Button size="sm" variant="ghost" onClick={onAddStage}>
            + stage
          </Button>
        </div>
        {stage === null ? (
          <p className="wfe-inspector__empty">Select a stage on the canvas.</p>
        ) : (
          <>
            <Input
              label="Id"
              value={stage.id}
              onChange={(e) => onStageField(stage.key, 'id', e.target.value)}
              invalid={fieldInvalid(stageIssueList, 'id')}
              {...(() => {
                const hint = stageIssueList.find((i) => i.field === 'id')?.message;
                return hint !== undefined ? { hint } : {};
              })()}
            />
            <Input
              label="Title"
              value={stage.title}
              onChange={(e) => onStageField(stage.key, 'title', e.target.value)}
            />
            <Input
              label="Instructions"
              multiline
              rows={6}
              value={stage.instructions}
              onChange={(e) => onStageField(stage.key, 'instructions', e.target.value)}
            />
            <Select
              label="Harness"
              value={stage.harness ?? ''}
              options={[{ value: '', label: '(required)' }, ...harnessOpts]}
              onChange={(e) => onStageField(stage.key, 'harness', e.target.value)}
              className={fieldInvalid(stageIssueList, 'harness') ? 'wfe-field--invalid' : undefined}
            />
            <Select
              label="Model"
              value={stage.model ?? ''}
              options={
                modelOpts.length > 0
                  ? [{ value: '', label: '(required)' }, ...modelOpts]
                  : [{ value: '', label: '(none)' }]
              }
              onChange={(e) => onStageField(stage.key, 'model', e.target.value)}
              disabled={modelOpts.length === 0}
              className={fieldInvalid(stageIssueList, 'model') ? 'wfe-field--invalid' : undefined}
            />
            <Select
              label="Worktree"
              value={stage.worktree ?? ''}
              options={worktreeSelectOptions(worktrees)}
              onChange={(e) => onStageField(stage.key, 'worktree', e.target.value)}
            />
            <Select
              label="Gate"
              value={stage.gate}
              options={[
                { value: 'auto', label: 'auto' },
                { value: 'human', label: 'human' },
                { value: 'conditional', label: 'conditional' },
              ]}
              onChange={(e) => onStageField(stage.key, 'gate', e.target.value)}
            />
            {stageIssueList.length > 0 ? (
              <ul className="wfe-inspector__issues">
                {stageIssueList.map((issue, i) => (
                  <li
                    key={`${issue.code}-${i}`}
                    className={
                      issue.severity === 'error'
                        ? 'wfe-inspector__issue wfe-inspector__issue--error'
                        : 'wfe-inspector__issue wfe-inspector__issue--warning'
                    }
                  >
                    {issue.message}
                  </li>
                ))}
              </ul>
            ) : null}
            <Button
              size="sm"
              variant="danger"
              onClick={() => onDeleteStage(stage.key)}
              className="wfe-inspector__delete"
            >
              Delete stage
            </Button>
          </>
        )}
      </div>
    </aside>
  );
}
