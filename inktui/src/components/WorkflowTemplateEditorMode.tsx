import { useBoundingClientRect, useOnClick, useOnWheel } from '@ink-tools/ink-mouse';
import type { DOMElement, Key } from 'ink';
import { Box, Text } from 'ink';
import { type JSX, useRef } from 'react';
import type { Keymap } from '../input/keymap.js';
import type { Mode, ModeHint, ModeStoreApi } from '../input/modeStore.js';
import '../input/dispatcher.js';
import { useAppStore } from '../hooks/useAppStore.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import { type CellSurface, createSurface, renderSurface } from '../render/cellSurface.js';
import {
  type HarnessModel,
  type HarnessModelsActions,
  modelsFor,
  STATIC_HARNESS_MODELS,
} from '../store/dialogs/harnessModelsActions.js';
import {
  MAIN_WORKTREE_KEY,
  NEW_WORKTREE_KEY,
  type WorktreeOption,
  type WorktreeOptionsActions,
} from '../store/dialogs/worktreeOptionsActions.js';
import type { AppStoreApi } from '../store/store.js';
import { selectTemplatesByName } from '../store/templates/templatesSlice.js';
import type { WorkflowRun } from '../store/workflowRuns/workflowRunsSlice.js';
import type { WorkflowTemplate } from '../store/workflows/workflowsSlice.js';
import { useTheme } from '../theme/themeStore.js';
import {
  compileWorkflowTemplate,
  type WizardField,
} from '../workflowEditor/compile.js';
import { type GraphLayout, layoutWorkflow } from '../workflowEditor/layout.js';
import type {
  EditableField,
  EditorIssue,
  EditorWorkflow,
  StageKey,
  Viewport,
} from '../workflowEditor/model.js';
import { workflowEqual } from '../workflowEditor/model.js';
import { autoPan, nearestNode } from '../workflowEditor/navigation.js';
import { paintWorkflow } from '../workflowEditor/paint.js';
import {
  applyWorkflowEdit,
  dependencyLegality,
  type HistoryEntry,
  type WorkflowEdit,
} from '../workflowEditor/reducer.js';
import { decodeStaticDagStatuses } from '../workflowEditor/runState.js';
import { validateEditorWorkflow } from '../workflowEditor/validate.js';
import { fromWire, toWire } from '../workflowEditor/wire.js';
import { useBottomBarLines } from './BottomBar.js';
import { TextRuns } from './TextRuns.js';
import { HARNESS_ORDER } from './spawnWizardMachine.js';

export const WORKFLOW_TEMPLATE_EDITOR_MODE_ID = 'workflow-editor';

type Interaction =
  | { readonly kind: 'normal' }
  | { readonly kind: 'connect'; readonly candidate: StageKey | null }
  | {
      readonly kind: 'stage-menu';
      readonly target: StageKey;
      readonly field: EditableField;
      readonly editing: boolean;
      readonly value: string;
    }
  | {
      readonly kind: 'edit';
      readonly target: StageKey | 'workflow';
      readonly field: EditableField;
      readonly value: string;
    }
  | {
      /** Generated run wizard: one field per distinct placeholder after prompt-template expansion. */
      readonly kind: 'wizard';
      readonly fields: readonly WizardField[];
      readonly values: Record<string, string>;
      readonly cursor: number;
    }
  | { readonly kind: 'search'; readonly query: string; readonly returnTo: 'normal' | 'connect' }
  | {
      readonly kind: 'delete';
      readonly stage: StageKey;
      readonly stageId: string;
      readonly affected: number;
    }
  | { readonly kind: 'conflict'; readonly remoteSummary: string }
  | { readonly kind: 'discard' };

interface Session {
  base: EditorWorkflow;
  draft: EditorWorkflow;
  originalName: string | null;
  selected: StageKey | null;
  viewport: Viewport;
  canvasWidth: number;
  canvasHeight: number;
  inspectorOpen: boolean;
  interaction: Interaction;
  undo: HistoryEntry[];
  redo: HistoryEntry[];
  status: 'idle' | 'saving' | 'conflict' | 'error';
  feedback: string | null;
  serverIssues: readonly EditorIssue[];
  harnessModels: Readonly<Record<string, readonly HarnessModel[]>>;
  worktrees: readonly WorktreeOption[];
}

export interface WorkflowTemplateEditorModeOptions {
  readonly workflow?: WorkflowTemplate;
  /** Called after a successfully saved definition, useful to focus a caller-owned list. */
  readonly onSaved?: (workflow: WorkflowTemplate) => void;
  readonly harnessModels?: HarnessModelsActions;
  readonly worktreeOptions?: WorktreeOptionsActions;
}

type WorkflowTemplateEditorIntent =
  | 'up'
  | 'down'
  | 'dependency'
  | 'dependent'
  | 'panUp'
  | 'panDown'
  | 'panLeft'
  | 'panRight'
  | 'inspect'
  | 'add'
  | 'connect'
  | 'delete'
  | 'undo'
  | 'redo'
  | 'save'
  | 'run'
  | 'inspector'
  | 'escape'
  | 'enter'
  | 'backspace'
  | 'argsNext'
  | 'argsPrev'
  | 'confirm'
  | 'cancel'
  | 'moveEarlier'
  | 'moveLater'
  | 'search'
  | 'workflowEdit'
  | 'reload'
  | 'overwrite'
  | 'saveAs';

function blankWorkflow(): EditorWorkflow {
  return { name: '', description: '', mode: 'static', stages: [] };
}

/** Full-screen local-draft workflow template editor. The canonical slice is written only by workflow.put. */
export function workflowTemplateEditorMode(
  modes: ModeStoreApi,
  app: AppStoreApi,
  options: WorkflowTemplateEditorModeOptions = {},
): Mode<WorkflowTemplateEditorIntent> {
  const existing = options.workflow ?? app.getState().workflows.items[0];
  const initial = existing === undefined ? blankWorkflow() : fromWire(existing);
  const s: Session = {
    base: initial,
    draft: initial,
    originalName: existing?.name ?? null,
    selected: initial.stages[0]?.key ?? null,
    viewport: { x: 0, y: 0 },
    canvasWidth: 80,
    canvasHeight: 20,
    inspectorOpen: true,
    interaction: { kind: 'normal' },
    undo: [],
    redo: [],
    status: 'idle',
    feedback: null,
    serverIssues: [],
    harnessModels: STATIC_HARNESS_MODELS,
    worktrees: [],
  };
  const id = WORKFLOW_TEMPLATE_EDITOR_MODE_ID;

  void options.harnessModels?.fetch().then((models) => {
    s.harnessModels = models;
    refresh();
  });
  void options.worktreeOptions?.fetch().then((worktrees) => {
    s.worktrees = worktrees;
    refresh();
  });

  function refresh(): void {
    const frame = modes.getState().stack.find((candidate) => candidate.mode.id === id);
    if (frame !== undefined) modes.getState().enter(frame.mode);
  }
  function apply(edit: WorkflowEdit): void {
    const next = applyWorkflowEdit(s.draft, edit);
    if (next === s.draft) return;
    s.undo = [...s.undo.slice(-99), { draft: s.draft, selected: s.selected }];
    s.redo = [];
    s.draft = next;
    s.serverIssues = [];
    s.feedback = null;
    s.status = 'idle';
    if (s.selected === null) s.selected = next.stages[0]?.key ?? null;
    refresh();
  }
  /** Clamp a free-text field value onto a pick list when the field has options. */
  function pickValue(
    interaction:
      | Extract<Interaction, { readonly kind: 'edit' }>
      | Extract<Interaction, { readonly kind: 'stage-menu' }>,
    value: string,
  ): string {
    const options = optionsForEditorField(s, interaction);
    if (options.length === 0) return value;
    if (options.includes(value)) return value;
    return options[0] ?? '';
  }
  /** After harness changes, keep model on a valid id for that harness (or clear it). */
  function syncModelForHarness(target: StageKey, harness: string): void {
    const models = modelsFor(harness, s.harnessModels);
    const stage = s.draft.stages.find((item) => item.key === target);
    if (stage === undefined) return;
    if (models.length === 0) {
      if (stage.model !== null && stage.model !== '')
        apply({ type: 'set-field', key: target, field: 'model', value: '' });
      return;
    }
    if (stage.model !== null && models.some((model) => model.id === stage.model)) return;
    apply({ type: 'set-field', key: target, field: 'model', value: models[0]?.id ?? '' });
  }
  function navigate(direction: 'up' | 'down' | 'dependency' | 'dependent'): void {
    const layout = layoutWorkflow(s.draft);
    const selected = s.interaction.kind === 'connect' ? s.interaction.candidate : s.selected;
    if (selected === null) return;
    const next = nearestNode(layout, selected, direction);
    if (next === null) return;
    if (s.interaction.kind === 'connect') s.interaction = { ...s.interaction, candidate: next };
    else s.selected = next;
    const rect = layout.nodes.get(next)?.rect;
    if (rect !== undefined) s.viewport = autoPan(s.viewport, rect, s.canvasWidth, s.canvasHeight);
    refresh();
  }
  function revealIssue(issue: EditorIssue | undefined): void {
    if (issue?.stageKey === undefined) return;
    focusStage(issue.stageKey);
  }
  function focusStage(stageKey: StageKey): void {
    s.selected = stageKey;
    const rect = layoutWorkflow(s.draft).nodes.get(stageKey)?.rect;
    if (rect !== undefined) {
      s.viewport = autoPan(s.viewport, rect, s.canvasWidth, s.canvasHeight);
    }
  }
  function stageFieldValue(target: StageKey, field: EditableField): string {
    const stage = s.draft.stages.find((item) => item.key === target);
    if (stage === undefined) return '';
    if (field === 'gate') return stage.gate;
    if (field === 'harness' || field === 'model' || field === 'worktree') return stage[field] ?? '';
    if (field === 'id' || field === 'title' || field === 'instructions') return stage[field];
    return '';
  }
  function startStageMenu(): void {
    if (s.selected === null) return;
    s.inspectorOpen = true;
    s.interaction = {
      kind: 'stage-menu',
      target: s.selected,
      field: 'title',
      editing: false,
      value: stageFieldValue(s.selected, 'title'),
    };
    refresh();
  }
  function moveStageMenuField(
    interaction: Extract<Interaction, { readonly kind: 'stage-menu' }>,
    delta: number,
  ): void {
    const fields: readonly EditableField[] = [
      'title',
      'instructions',
      'harness',
      'model',
      'worktree',
      'gate',
      'id',
    ];
    const current = fields.indexOf(interaction.field);
    const field = fields[(current + delta + fields.length) % fields.length];
    if (field === undefined) return;
    s.interaction = {
      ...interaction,
      field,
      editing: false,
      value: stageFieldValue(interaction.target, field),
    };
    refresh();
  }
  function nextEditableField(
    interaction: Extract<Interaction, { readonly kind: 'edit' }>,
    delta: number,
  ): void {
    const fields: readonly EditableField[] =
      interaction.target === 'workflow'
        ? ['name', 'description', 'mode']
        : ['title', 'id', 'instructions', 'harness', 'model', 'worktree', 'gate'];
    const current = fields.indexOf(interaction.field);
    const field = fields[(current + delta + fields.length) % fields.length];
    if (field === undefined) return;
    const stage =
      interaction.target === 'workflow'
        ? undefined
        : s.draft.stages.find((item) => item.key === interaction.target);
    const value =
      interaction.target === 'workflow'
        ? String(s.draft[field as 'name' | 'description' | 'mode'])
        : field === 'gate'
          ? (stage?.gate ?? '')
          : field === 'harness' || field === 'model' || field === 'worktree'
            ? (stage?.[field] ?? '')
            : (stage?.[field as 'id' | 'title' | 'instructions'] ?? '');
    const next = { ...interaction, field, value };
    s.interaction = { ...next, value: pickValue(next, value) };
    refresh();
  }
  function save(afterSave?: () => void): void {
    const local = validateEditorWorkflow(s.draft);
    const error = local.find((issue) => issue.severity === 'error');
    if (error !== undefined) {
      s.serverIssues = [];
      s.feedback = error.message;
      s.status = 'error';
      revealIssue(error);
      refresh();
      return;
    }
    s.status = 'saving';
    s.feedback = null;
    refresh();
    void app
      .getState()
      .actions.workflows.put(toWire(s.draft), s.originalName)
      .then((result) => {
        if (!result.ok || result.workflow == null) {
          s.status = result.conflict === true ? 'conflict' : 'error';
          if (result.conflict === true) {
            const remote = result.workflows.find(
              (workflow) => workflow.name === s.originalName || workflow.name === s.draft.name,
            );
            s.interaction = {
              kind: 'conflict',
              remoteSummary:
                remote === undefined
                  ? `Latest registry revision ${result.revision}; this workflow was removed.`
                  : `Latest ${remote.name} v${remote.definition_version ?? 1} has ${remote.stages?.length ?? 0} stage${remote.stages?.length === 1 ? '' : 's'} (registry ${result.revision}).`,
            };
          }
          s.serverIssues = (result.issues ?? []).map((issue) =>
            editorIssueFromServer(s.draft, issue),
          );
          s.feedback = s.serverIssues[0]?.message ?? 'Unable to save workflow.';
          revealIssue(s.serverIssues[0]);
          refresh();
          return;
        }
        const canonical = fromWire(result.workflow);
        s.base = canonical;
        s.draft = canonical;
        s.originalName = canonical.name;
        s.status = 'idle';
        s.serverIssues = [];
        s.feedback = null;
        options.onSaved?.(result.workflow);
        refresh();
        afterSave?.();
      })
      .catch((error: unknown) => {
        s.status = 'error';
        s.serverIssues = [
          {
            code: 'invalid_name',
            severity: 'error',
            message: error instanceof Error ? error.message : String(error),
          },
        ];
        s.feedback = s.serverIssues[0]?.message ?? 'Unable to save workflow.';
        refresh();
      });
  }
  function run(): void {
    const localIssues = validateEditorWorkflow(s.draft);
    const blockingIssue = localIssues.find(
      (issue) =>
        issue.severity === 'error' ||
        issue.code === 'unsupported_mode' ||
        issue.code === 'unsupported_gate',
    );
    if (blockingIssue !== undefined) {
      s.serverIssues = [];
      s.feedback = blockingIssue.message;
      s.status = 'error';
      revealIssue(blockingIssue);
      refresh();
      return;
    }
    const runWith = (args: Record<string, string>): void => {
      // Start uses the existing RPC; server snapshots the saved definition so later template edits
      // cannot rewrite an already-started run. Client compile is wizard/preview only until a
      // workflow.compile RPC exists in the generated protocol.
      void app.getState().actions.workflows.run(s.draft.name, args);
      s.interaction = { kind: 'normal' };
      refresh();
    };
    const ask = (): void => {
      const templateBodies = new Map(
        [...selectTemplatesByName(app.getState().templates.items)].map(([name, record]) => [
          name,
          record.body,
        ]),
      );
      const compiled = compileWorkflowTemplate(s.draft, templateBodies);
      if (compiled.issues.length > 0) {
        const first = compiled.issues[0];
        s.serverIssues = [];
        s.feedback = first?.message ?? 'Workflow template compile failed.';
        s.status = 'error';
        if (first?.stageKey !== undefined) focusStage(first.stageKey);
        refresh();
        return;
      }
      if (compiled.fields.length === 0) {
        runWith({});
        return;
      }
      s.feedback = null;
      s.status = 'idle';
      s.interaction = {
        kind: 'wizard',
        fields: compiled.fields,
        values: Object.fromEntries(
          compiled.fields.map((field) => [field.name, field.defaultValue]),
        ),
        cursor: 0,
      };
      refresh();
    };
    if (!workflowEqual(s.base, s.draft)) save(ask);
    else ask();
  }
  function close(): void {
    modes.getState().exit(id);
  }

  return {
    id,
    presentation: 'fullscreen',
    get hints(): readonly ModeHint[] {
      return hints(s.interaction, fieldOptions(s, s.interaction));
    },
    get keymap(): Keymap<WorkflowTemplateEditorIntent> {
      return workflowTemplateEditorKeymap(s.interaction, fieldOptions(s, s.interaction).length > 0);
    },
    onIntent(intent): void {
      if (s.interaction.kind === 'conflict') {
        if (intent === 'reload') {
          void app
            .getState()
            .actions.workflows.load()
            .then(() => {
              const remote = app
                .getState()
                .workflows.items.find(
                  (workflow) => workflow.name === s.originalName || workflow.name === s.draft.name,
                );
              const reloaded = remote === undefined ? blankWorkflow() : fromWire(remote);
              s.base = reloaded;
              s.draft = reloaded;
              s.originalName = remote?.name ?? null;
              s.selected = reloaded.stages[0]?.key ?? null;
              s.undo = [];
              s.redo = [];
              s.status = 'idle';
              s.serverIssues = [];
              s.feedback = null;
              s.interaction = { kind: 'normal' };
              refresh();
            });
        } else if (intent === 'overwrite') {
          void app
            .getState()
            .actions.workflows.load()
            .then(() => {
              s.interaction = { kind: 'normal' };
              save();
            });
        } else if (intent === 'saveAs') {
          s.originalName = null;
          s.status = 'idle';
          s.interaction = {
            kind: 'edit',
            target: 'workflow',
            field: 'name',
            value: s.draft.name,
          };
          refresh();
        } else if (intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        }
        return;
      }
      if (s.interaction.kind === 'discard') {
        if (intent === 'confirm') close();
        else if (intent === 'cancel' || intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        }
        return;
      }
      if (s.interaction.kind === 'delete') {
        if (intent === 'confirm') {
          apply({ type: 'delete-stage', key: s.interaction.stage });
          s.selected = s.draft.stages[0]?.key ?? null;
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'cancel' || intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        }
        return;
      }
      if (s.interaction.kind === 'connect') {
        if (intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
          return;
        }
        if (intent === 'enter' && s.selected !== null && s.interaction.candidate !== null) {
          apply({ type: 'toggle-dependency', target: s.selected, source: s.interaction.candidate });
          return;
        }
        if (intent === 'search') {
          s.interaction = { kind: 'search', query: '', returnTo: 'connect' };
          refresh();
          return;
        }
        if (intent === 'up') navigate('up');
        else if (intent === 'down') navigate('down');
        else if (intent === 'dependency') navigate('dependency');
        else if (intent === 'dependent') navigate('dependent');
        return;
      }
      if (s.interaction.kind === 'stage-menu') {
        if (s.interaction.editing) {
          if (intent === 'enter') {
            const editing = s.interaction;
            const previous =
              editing.field === 'harness'
                ? (s.draft.stages.find((item) => item.key === editing.target)?.harness ?? '')
                : null;
            apply({
              type: 'set-field',
              key: editing.target,
              field: editing.field,
              value: editing.value,
            });
            if (editing.field === 'harness' && previous !== editing.value)
              syncModelForHarness(editing.target, editing.value);
            s.interaction = { ...editing, editing: false };
            refresh();
          } else if (intent === 'escape') {
            s.interaction = {
              ...s.interaction,
              editing: false,
              value: stageFieldValue(s.interaction.target, s.interaction.field),
            };
            refresh();
          } else if (intent === 'backspace') {
            if (optionsForEditorField(s, s.interaction).length > 0) return;
            s.interaction = { ...s.interaction, value: s.interaction.value.slice(0, -1) };
            refresh();
          } else if (intent === 'up' || intent === 'down') {
            const options = optionsForEditorField(s, s.interaction);
            if (options.length > 0) {
              const current = options.indexOf(s.interaction.value);
              const delta = intent === 'down' ? 1 : -1;
              const index =
                current < 0
                  ? delta > 0
                    ? 0
                    : options.length - 1
                  : (current + delta + options.length) % options.length;
              const value = options[index];
              if (value !== undefined) s.interaction = { ...s.interaction, value };
              refresh();
            }
          }
        } else if (intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'enter') {
          const value = pickValue(
            s.interaction,
            stageFieldValue(s.interaction.target, s.interaction.field),
          );
          s.interaction = {
            ...s.interaction,
            editing: true,
            value,
          };
          refresh();
        } else if (intent === 'up') moveStageMenuField(s.interaction, -1);
        else if (intent === 'down') moveStageMenuField(s.interaction, 1);
        return;
      }
      if (s.interaction.kind === 'edit') {
        if (intent === 'enter') {
          const editing = s.interaction;
          const previous =
            editing.target !== 'workflow' && editing.field === 'harness'
              ? (s.draft.stages.find((item) => item.key === editing.target)?.harness ?? '')
              : null;
          apply({
            type: 'set-field',
            key: editing.target,
            field: editing.field,
            value: editing.value,
          });
          if (
            editing.target !== 'workflow' &&
            editing.field === 'harness' &&
            previous !== editing.value
          )
            syncModelForHarness(editing.target, editing.value);
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'backspace') {
          if (optionsForEditorField(s, s.interaction).length > 0) return;
          s.interaction = { ...s.interaction, value: s.interaction.value.slice(0, -1) };
          refresh();
        } else if (intent === 'up' || intent === 'down') {
          const options = optionsForEditorField(s, s.interaction);
          if (options.length > 0) {
            const current = options.indexOf(s.interaction.value);
            const delta = intent === 'down' ? 1 : -1;
            const index =
              current < 0
                ? delta > 0
                  ? 0
                  : options.length - 1
                : (current + delta + options.length) % options.length;
            const value = options[index];
            if (value !== undefined) s.interaction = { ...s.interaction, value };
            refresh();
          }
        } else if (intent === 'argsNext') nextEditableField(s.interaction, 1);
        else if (intent === 'argsPrev') nextEditableField(s.interaction, -1);
        return;
      }
      if (s.interaction.kind === 'wizard') {
        if (intent === 'enter') {
          void app.getState().actions.workflows.run(s.draft.name, s.interaction.values);
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'argsNext') {
          s.interaction = {
            ...s.interaction,
            cursor: (s.interaction.cursor + 1) % s.interaction.fields.length,
          };
          refresh();
        } else if (intent === 'argsPrev') {
          s.interaction = {
            ...s.interaction,
            cursor:
              (s.interaction.cursor - 1 + s.interaction.fields.length) %
              s.interaction.fields.length,
          };
          refresh();
        } else if (intent === 'backspace') {
          const field = s.interaction.fields[s.interaction.cursor];
          if (field === undefined) return;
          s.interaction.values[field.name] = (s.interaction.values[field.name] ?? '').slice(0, -1);
          refresh();
        }
        return;
      }
      if (s.interaction.kind === 'search') {
        const query = s.interaction.query;
        if (intent === 'escape') {
          s.interaction =
            s.interaction.returnTo === 'connect'
              ? { kind: 'connect', candidate: s.selected }
              : { kind: 'normal' };
          refresh();
        } else if (intent === 'enter') {
          const match = s.draft.stages.find((stage) =>
            `${stage.id} ${stage.title}`.toLowerCase().includes(query.toLowerCase()),
          );
          if (s.interaction.returnTo === 'connect') {
            s.interaction = { kind: 'connect', candidate: match?.key ?? s.selected };
          } else {
            if (match !== undefined) s.selected = match.key;
            s.interaction = { kind: 'normal' };
          }
          refresh();
        } else if (intent === 'backspace') {
          s.interaction = { ...s.interaction, query: query.slice(0, -1) };
          refresh();
        }
        return;
      }
      if (intent === 'up') navigate('up');
      else if (intent === 'down') navigate('down');
      else if (intent === 'dependency') navigate('dependency');
      else if (intent === 'dependent') navigate('dependent');
      else if (intent === 'panUp') {
        s.viewport = { ...s.viewport, y: Math.max(0, s.viewport.y - 2) };
        refresh();
      } else if (intent === 'panDown') {
        s.viewport = { ...s.viewport, y: s.viewport.y + 2 };
        refresh();
      } else if (intent === 'panLeft') {
        s.viewport = { ...s.viewport, x: Math.max(0, s.viewport.x - 4) };
        refresh();
      } else if (intent === 'panRight') {
        s.viewport = { ...s.viewport, x: s.viewport.x + 4 };
        refresh();
      } else if (intent === 'enter') startStageMenu();
      else if (intent === 'add') apply({ type: 'add-stage', after: s.selected });
      else if (intent === 'connect' && s.selected !== null)
        s.interaction = { kind: 'connect', candidate: s.selected };
      else if (intent === 'delete' && s.selected !== null) {
        const stage = s.draft.stages.find((item) => item.key === s.selected);
        const affected =
          stage === undefined
            ? 0
            : s.draft.stages.reduce(
                (count, item) =>
                  count + item.dependsOn.filter((dependency) => dependency === stage.id).length,
                0,
              );
        s.interaction = {
          kind: 'delete',
          stage: s.selected,
          stageId: stage?.id ?? '',
          affected,
        };
        refresh();
      } else if (intent === 'undo') {
        const previous = s.undo.pop();
        if (previous !== undefined) {
          s.redo.push({ draft: s.draft, selected: s.selected });
          s.draft = previous.draft;
          s.selected = previous.selected;
          refresh();
        }
      } else if (intent === 'redo') {
        const next = s.redo.pop();
        if (next !== undefined) {
          s.undo.push({ draft: s.draft, selected: s.selected });
          s.draft = next.draft;
          s.selected = next.selected;
          refresh();
        }
      } else if (intent === 'save') save();
      else if (intent === 'run') run();
      else if (intent === 'inspector') startStageMenu();
      else if (intent === 'search') {
        s.interaction = { kind: 'search', query: '', returnTo: 'normal' };
        refresh();
      } else if (intent === 'workflowEdit') {
        s.interaction = { kind: 'edit', target: 'workflow', field: 'name', value: s.draft.name };
        refresh();
      } else if (intent === 'moveEarlier' && s.selected !== null)
        apply({ type: 'move-stage', key: s.selected, delta: -1 });
      else if (intent === 'moveLater' && s.selected !== null)
        apply({ type: 'move-stage', key: s.selected, delta: 1 });
      else if (intent === 'escape') {
        if (workflowEqual(s.base, s.draft)) close();
        else {
          s.interaction = { kind: 'discard' };
          refresh();
        }
      }
    },
    onUncaptured(input: string, _key: Key): boolean {
      if (input.length !== 1) return false;
      if (s.interaction.kind === 'edit') {
        if (optionsForEditorField(s, s.interaction).length > 0) return true;
        s.interaction = { ...s.interaction, value: s.interaction.value + input };
        refresh();
        return true;
      }
      if (s.interaction.kind === 'stage-menu' && s.interaction.editing) {
        if (optionsForEditorField(s, s.interaction).length > 0) return true;
        s.interaction = { ...s.interaction, value: s.interaction.value + input };
        refresh();
        return true;
      }
      if (s.interaction.kind === 'wizard') {
        const field = s.interaction.fields[s.interaction.cursor];
        if (field === undefined) return false;
        s.interaction.values[field.name] = (s.interaction.values[field.name] ?? '') + input;
        refresh();
        return true;
      }
      if (s.interaction.kind === 'search') {
        s.interaction = { ...s.interaction, query: s.interaction.query + input };
        refresh();
        return true;
      }
      return false;
    },
    render: () => (
      <WorkflowTemplateEditorSurface
        session={s}
        onSelect={(key) => {
          s.selected = key;
          refresh();
        }}
        onScroll={(delta) => {
          s.viewport = { ...s.viewport, y: Math.max(0, s.viewport.y + delta) };
          refresh();
        }}
      />
    ),
  };
}

function workflowTemplateEditorKeymap(
  interaction: Interaction,
  hasOptions = false,
): Keymap<WorkflowTemplateEditorIntent> {
  const escapeBinding = {
    chord: { key: { escape: true } },
    intent: 'escape',
    description: 'cancel',
  } as const;
  const enter = {
    chord: { key: { return: true } },
    intent: 'enter',
    description: 'confirm',
  } as const;
  const backspace = {
    chord: { key: { backspace: true } },
    intent: 'backspace',
    description: 'delete character',
  } as const;
  const tab = {
    chord: { key: { tab: true } },
    intent: 'argsNext',
    description: 'next field',
  } as const;
  const backtab = {
    chord: { key: { shift: true, tab: true } },
    intent: 'argsPrev',
    description: 'previous field',
  } as const;
  const optionArrows = [
    { chord: { key: { upArrow: true } }, intent: 'up', description: 'previous option' },
    { chord: { key: { downArrow: true } }, intent: 'down', description: 'next option' },
  ] as const;
  if (interaction.kind === 'search' || interaction.kind === 'wizard') {
    return [escapeBinding, enter, backspace, tab, backtab];
  }
  if (interaction.kind === 'edit') {
    return hasOptions
      ? [escapeBinding, enter, tab, backtab, ...optionArrows]
      : [escapeBinding, enter, backspace, tab, backtab, ...optionArrows];
  }
  if (interaction.kind === 'stage-menu') {
    if (interaction.editing) {
      return hasOptions
        ? [escapeBinding, enter, ...optionArrows]
        : [
            escapeBinding,
            enter,
            backspace,
            { chord: { key: { upArrow: true } }, intent: 'up', description: 'previous option' },
            { chord: { key: { downArrow: true } }, intent: 'down', description: 'next option' },
          ];
    }
    return [
      escapeBinding,
      enter,
      {
        chord: [{ input: 'k' }, { key: { upArrow: true } }],
        intent: 'up',
        description: 'previous field',
      },
      {
        chord: [{ input: 'j' }, { key: { downArrow: true } }],
        intent: 'down',
        description: 'next field',
      },
    ];
  }
  if (interaction.kind === 'conflict') {
    return [
      escapeBinding,
      { chord: { input: 'r' }, intent: 'reload', description: 'reload remote' },
      { chord: { input: 'o' }, intent: 'overwrite', description: 'overwrite latest' },
      { chord: { input: 'A' }, intent: 'saveAs', description: 'save as' },
    ];
  }
  if (interaction.kind === 'delete' || interaction.kind === 'discard') {
    return [
      escapeBinding,
      { chord: { input: 'y' }, intent: 'confirm', description: 'confirm' },
      { chord: { input: 'n' }, intent: 'cancel', description: 'cancel' },
    ];
  }
  const navigation: Keymap<WorkflowTemplateEditorIntent> = [
    { chord: { input: 'j' }, intent: 'down', description: 'next' },
    { chord: { input: 'k' }, intent: 'up', description: 'previous' },
    { chord: { input: 'h' }, intent: 'dependency', description: 'dependency' },
    { chord: { input: 'l' }, intent: 'dependent', description: 'dependent' },
    enter,
    escapeBinding,
  ];
  if (interaction.kind === 'connect') {
    return [
      ...navigation,
      { chord: { input: '/' }, intent: 'search', description: 'search candidate' },
    ];
  }
  return [
    ...navigation,
    { chord: { key: { upArrow: true } }, intent: 'panUp', description: 'pan' },
    { chord: { key: { downArrow: true } }, intent: 'panDown', description: 'pan' },
    { chord: { key: { leftArrow: true } }, intent: 'panLeft', description: 'pan' },
    { chord: { key: { rightArrow: true } }, intent: 'panRight', description: 'pan' },
    { chord: { input: 'a' }, intent: 'add', description: 'add stage' },
    { chord: { input: 'c' }, intent: 'connect', description: 'dependencies' },
    { chord: { input: 'x' }, intent: 'delete', description: 'delete stage' },
    { chord: { input: 'u' }, intent: 'undo', description: 'undo' },
    { chord: { input: 'r', key: { ctrl: true } }, intent: 'redo', description: 'redo' },
    { chord: { input: 's' }, intent: 'save', description: 'save' },
    { chord: { input: 'R' }, intent: 'run', description: 'save & run' },
    { chord: { input: 'i' }, intent: 'inspector', description: 'edit stage' },
    { chord: { input: '/' }, intent: 'search', description: 'search' },
    { chord: { input: 'w' }, intent: 'workflowEdit', description: 'workflow fields' },
    {
      chord: { input: 'j', key: { meta: true } },
      intent: 'moveLater',
      description: 'move later',
    },
    {
      chord: { input: 'k', key: { meta: true } },
      intent: 'moveEarlier',
      description: 'move earlier',
    },
  ];
}

function hints(
  interaction: Interaction,
  options: readonly string[] = [],
): readonly ModeHint[] {
  if (interaction.kind === 'stage-menu')
    return interaction.editing
      ? options.length > 0
        ? [
            { key: '↑/↓', description: 'select option' },
            { key: 'enter', description: 'apply' },
            { key: 'esc', description: 'cancel field' },
          ]
        : [
            { key: 'type', description: 'edit value' },
            { key: 'enter', description: 'apply' },
            { key: 'esc', description: 'cancel field' },
          ]
      : [
          { key: 'j/k', description: 'select field' },
          { key: 'enter', description: 'edit field' },
          { key: 'esc', description: 'close editor' },
        ];
  if (interaction.kind === 'edit')
    return options.length > 0
      ? [
          { key: '↑/↓', description: 'select option' },
          { key: 'tab', description: 'next field' },
          { key: 'enter', description: 'commit' },
          { key: 'esc', description: 'cancel' },
        ]
      : [
          { key: 'type', description: 'edit field' },
          { key: 'tab', description: 'next field' },
          { key: 'enter', description: 'commit' },
          { key: 'esc', description: 'cancel' },
        ];
  if (interaction.kind === 'connect')
    return [
      { key: 'hjkl', description: 'pick candidate' },
      { key: 'enter', description: 'toggle edge' },
      { key: '/', description: 'search' },
      { key: 'esc', description: 'done' },
    ];
  if (interaction.kind === 'wizard')
    return [
      { key: 'type', description: 'input value' },
      { key: 'tab', description: 'next field' },
      { key: 'enter', description: 'run' },
      { key: 'esc', description: 'cancel' },
    ];
  if (interaction.kind === 'search')
    return [
      { key: 'type', description: 'filter stages' },
      { key: 'enter', description: 'select' },
      { key: 'esc', description: 'cancel' },
    ];
  if (interaction.kind === 'conflict')
    return [
      { key: 'r', description: 'reload remote' },
      { key: 'o', description: 'overwrite latest' },
      { key: 'A', description: 'save as' },
      { key: 'esc', description: 'cancel' },
    ];
  if (interaction.kind === 'delete')
    return [
      { key: 'y', description: 'delete' },
      { key: 'n/esc', description: 'cancel' },
    ];
  if (interaction.kind === 'discard')
    return [
      { key: 'y', description: 'discard' },
      { key: 'n/esc', description: 'keep editing' },
    ];
  return [
    { key: 'hjkl', description: 'navigate graph' },
    { key: 'enter/i', description: 'edit stage' },
    { key: 'a', description: 'add stage' },
    { key: 'c', description: 'dependencies' },
    { key: 'x', description: 'delete' },
    { key: 's', description: 'save' },
    { key: 'R', description: 'run' },
    { key: 'arrows', description: 'pan' },
    { key: 'esc', description: 'close' },
  ];
}

function WorkflowTemplateEditorSurface({
  session,
  onSelect,
  onScroll,
}: {
  readonly session: Session;
  readonly onSelect: (key: StageKey) => void;
  readonly onScroll: (delta: number) => void;
}): JSX.Element {
  const { columns, rows } = useTerminalSize();
  const footerLines = useBottomBarLines().length;
  // Shell keeps the BottomBar under fullscreen modes; size the canvas to the body slot above it.
  const availRows = Math.max(8, rows - footerLines);
  const theme = useTheme();
  const activeRun = useAppStore((state) => state.workflowRuns.activeRun);
  const run =
    activeRun?.definition_name === session.draft.name ||
    activeRun?.definition_name === session.originalName
      ? activeRun
      : null;
  const stageStatuses = run === null ? new Map() : decodeStaticDagStatuses(run.state);
  // A running workflow is immutable execution history: graph geometry must come from its definition
  // snapshot, never from a definition the user has subsequently edited in this mode.
  const runSnapshot = run === null ? null : editorWorkflowFromSnapshot(run.definition_snapshot);
  const displayed = runSnapshot ?? session.draft;
  const selectedId = session.draft.stages.find((stage) => stage.key === session.selected)?.id;
  const displayedSelected =
    displayed.stages.find((stage) => stage.id === selectedId)?.key ??
    displayed.stages[0]?.key ??
    null;
  const layout = layoutWorkflow(displayed);
  const issues =
    runSnapshot === null ? [...validateEditorWorkflow(session.draft), ...session.serverIssues] : [];
  const issuesByNode = new Map<StageKey, EditorIssue[]>();
  for (const issue of issues) {
    if (issue.stageKey === undefined) continue;
    const nodeIssues = issuesByNode.get(issue.stageKey) ?? [];
    nodeIssues.push(issue);
    issuesByNode.set(issue.stageKey, nodeIssues);
  }
  const narrow = columns < 72;
  const overlay = columns >= 72 && columns < 110;
  const inspectorWidth = !narrow && !overlay && session.inspectorOpen ? 36 : 0;
  const canvasWidth = Math.max(20, columns - inspectorWidth);
  const canvasHeight = Math.max(5, availRows - 4);
  session.canvasWidth = canvasWidth;
  session.canvasHeight = canvasHeight;
  const connect =
    runSnapshot === null &&
    session.interaction.kind === 'connect' &&
    session.selected !== null &&
    session.interaction.candidate !== null
      ? {
          target: session.selected,
          candidate: session.interaction.candidate,
          legality: dependencyLegality(
            session.draft,
            session.selected,
            session.interaction.candidate,
          ),
        }
      : undefined;
  const surface = paintWorkflow(
    createSurface(canvasWidth, canvasHeight),
    layout,
    session.viewport,
    displayedSelected,
    {
      edge: { fg: theme.muted },
      cycleEdge: { fg: theme.error },
      node: { fg: theme.text },
      selected: { fg: theme.accent, bold: true },
      invalid: { fg: theme.error },
      cycle: { fg: theme.error, bold: true },
      dependency: { fg: theme.warning },
      dependent: { fg: theme.heading },
      candidateAdd: { fg: theme.success, bold: true },
      candidateRemove: { fg: theme.warning, bold: true },
      candidateIllegal: { fg: theme.error, bold: true },
      runtimeStatuses: stageStatuses,
      issuesByNode,
      runtime: {
        blocked: { fg: theme.muted },
        ready: { fg: theme.active },
        requested: { fg: theme.heading },
        running: { fg: theme.accent, bold: true },
        waiting_approval: { fg: theme.warning, bold: true },
        succeeded: { fg: theme.success },
        failed: { fg: theme.error, bold: true },
        cancelled: { fg: theme.inactive },
      },
      ...(connect === undefined ? {} : { connect }),
    },
  );
  const selected = displayed.stages.find((stage) => stage.key === displayedSelected) ?? null;
  const wizard = session.interaction.kind === 'wizard' ? session.interaction : null;
  const editOptions =
    session.interaction.kind === 'edit' ? optionsForEditorField(session, session.interaction) : [];
  const editValue = session.interaction.kind === 'edit' ? session.interaction.value : '';
  const stageMenuOptions =
    session.interaction.kind === 'stage-menu'
      ? optionsForEditorField(session, session.interaction)
      : [];
  return (
    <Box width={columns} height={availRows} flexDirection="column" overflow="hidden">
      <Text
        bold
        color={theme.accent}
      >{`WORKFLOWS  ${session.draft.name || '(unnamed)'}${workflowEqual(session.base, session.draft) ? '' : ' • unsaved'}  ${session.status}${run === null ? '' : `  run: ${run.status}`}`}</Text>
      {session.interaction.kind === 'discard' ? (
        <Text color={theme.warning}>Discard unsaved edits? y: discard n/esc: continue</Text>
      ) : null}
      {session.feedback !== null ? <Text color={theme.error}>{session.feedback}</Text> : null}
      {session.interaction.kind === 'delete' ? (
        <Text
          color={theme.warning}
        >{`Delete “${session.interaction.stageId || '(blank)'}” and remove ${session.interaction.affected} dependency reference${session.interaction.affected === 1 ? '' : 's'}? y: delete  n/esc: cancel`}</Text>
      ) : null}
      {session.interaction.kind === 'edit' ? (
        <>
          <Text
            color={theme.accent}
          >{`${session.interaction.field}: ${session.interaction.value}█`}</Text>
          {editOptions.length > 0 ? (
            <Text
              dimColor
            >{`Options (↑/↓): ${formatOptionList(editOptions, editValue)}`}</Text>
          ) : null}
        </>
      ) : null}
      {session.interaction.kind === 'search' ? (
        <Text color={theme.accent}>{`Search stages: ${session.interaction.query}█`}</Text>
      ) : null}
      {session.interaction.kind === 'conflict' ? (
        <>
          <Text
            color={theme.warning}
          >{`Registry changed remotely. ${session.interaction.remoteSummary}`}</Text>
          <Text color={theme.warning}>
            Review latest summary, then r: reload o: overwrite latest A: save as
          </Text>
        </>
      ) : null}
      {connect !== undefined ? (
        <Text
          color={
            connect.legality === 'add'
              ? theme.success
              : connect.legality === 'remove'
                ? theme.warning
                : theme.error
          }
        >{`${connect.legality.toUpperCase()} dependency`}</Text>
      ) : null}
      {wizard !== null ? (
        <Text
          color={theme.accent}
        >{`Run  ${wizard.fields
          .map((field, index) => {
            const value = wizard.values[field.name] ?? '';
            const cell = `${field.label}=${value}`;
            return index === wizard.cursor ? `[${cell}]` : cell;
          })
          .join('  ')}`}</Text>
      ) : null}
      {runSnapshot !== null ? (
        <Text
          dimColor
        >{`Monitoring immutable run snapshot v${run?.definition_version ?? '?'}`}</Text>
      ) : null}
      {narrow && session.interaction.kind === 'stage-menu' ? (
        <WorkflowInspector
          stage={selected}
          issues={issues}
          run={run}
          statuses={stageStatuses}
          order={
            displayedSelected === null
              ? undefined
              : displayed.stages.findIndex((stage) => stage.key === displayedSelected) + 1
          }
          total={displayed.stages.length}
          width={columns}
          interaction={session.interaction}
          editorOptions={stageMenuOptions}
        />
      ) : narrow ? (
        <WorkflowOutline
          draft={displayed}
          selected={displayedSelected}
          issues={issues}
          statuses={stageStatuses}
        />
      ) : (
        <Box flexDirection="row" flexGrow={1}>
          <WorkflowCanvas
            surface={surface}
            layout={layout}
            viewport={session.viewport}
            width={canvasWidth}
            height={canvasHeight}
            onSelect={onSelect}
            onScroll={onScroll}
          />
          {!overlay && session.inspectorOpen ? (
            <WorkflowInspector
              stage={selected}
              issues={issues}
              run={run}
              statuses={stageStatuses}
              order={
                displayedSelected === null
                  ? undefined
                  : displayed.stages.findIndex((stage) => stage.key === displayedSelected) + 1
              }
              total={displayed.stages.length}
              width={inspectorWidth}
              {...(session.interaction.kind === 'stage-menu'
                ? { interaction: session.interaction }
                : {})}
              editorOptions={stageMenuOptions}
            />
          ) : null}
          {overlay && session.inspectorOpen ? (
            <Box position="absolute" alignSelf="flex-end">
              <WorkflowInspector
                stage={selected}
                issues={issues}
                run={run}
                statuses={stageStatuses}
                order={
                  displayedSelected === null
                    ? undefined
                    : displayed.stages.findIndex((stage) => stage.key === displayedSelected) + 1
                }
                total={displayed.stages.length}
                width={Math.min(44, columns - 4)}
                {...(session.interaction.kind === 'stage-menu'
                  ? { interaction: session.interaction }
                  : {})}
                editorOptions={stageMenuOptions}
              />
            </Box>
          ) : null}
        </Box>
      )}
    </Box>
  );
}

function WorkflowCanvas({
  surface,
  layout,
  viewport,
  width,
  height,
  onSelect,
  onScroll,
}: {
  readonly surface: CellSurface;
  readonly layout: GraphLayout;
  readonly viewport: Viewport;
  readonly width: number;
  readonly height: number;
  readonly onSelect: (key: StageKey) => void;
  readonly onScroll: (delta: number) => void;
}): JSX.Element {
  const ref = useRef<DOMElement>(null);
  const bounds = useBoundingClientRect(ref, [width, height, viewport.x, viewport.y]);
  useOnClick(ref, (event) => {
    if (event.button !== 'left') return;
    const worldX = event.x - bounds.left + viewport.x;
    const worldY = event.y - bounds.top + viewport.y;
    for (const node of layout.nodes.values()) {
      const { rect } = node;
      if (
        worldX >= rect.x &&
        worldX < rect.x + rect.width &&
        worldY >= rect.y &&
        worldY < rect.y + rect.height
      ) {
        onSelect(node.key);
        return;
      }
    }
  });
  useOnWheel(ref, (event) => {
    if (event.button === 'wheel-up') onScroll(-3);
    else if (event.button === 'wheel-down') onScroll(3);
  });
  return (
    <Box ref={ref} flexDirection="column" width={width} height={height} overflow="hidden">
      {Array.from({ length: surface.height }, (_, y) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: fixed raster rows never reorder; y is their identity.
        <TextRuns key={y} runs={renderSurface(surface, y)} />
      ))}
    </Box>
  );
}

function fieldOptions(session: Session, interaction: Interaction): readonly string[] {
  if (interaction.kind !== 'edit' && interaction.kind !== 'stage-menu') return [];
  if (interaction.kind === 'stage-menu' && !interaction.editing) return [];
  return optionsForEditorField(session, interaction);
}

function formatOptionList(options: readonly string[], selected: string): string {
  return options.map((option) => (option === selected ? `[${option}]` : option)).join(' · ');
}

function optionsForEditorField(
  session: Session,
  interaction:
    | Extract<Interaction, { readonly kind: 'edit' }>
    | Extract<Interaction, { readonly kind: 'stage-menu' }>,
): readonly string[] {
  if (interaction.target === 'workflow') {
    return interaction.field === 'mode' ? ['static', 'generative'] : [];
  }
  const stage = session.draft.stages.find((candidate) => candidate.key === interaction.target);
  if (interaction.field === 'harness') {
    const known = new Set(Object.keys(session.harnessModels));
    const ordered = HARNESS_ORDER.filter((harness) => known.has(harness));
    const extras = [...known]
      .filter((harness) => !(HARNESS_ORDER as readonly string[]).includes(harness))
      .sort();
    return ordered.length > 0 || extras.length > 0 ? [...ordered, ...extras] : [...HARNESS_ORDER];
  }
  if (interaction.field === 'model' && stage !== undefined) {
    return modelsFor(stage.harness ?? '', session.harnessModels).map((model) => model.id);
  }
  if (interaction.field === 'worktree') {
    return session.worktrees.flatMap((worktree) =>
      worktree.key === MAIN_WORKTREE_KEY || worktree.key === NEW_WORKTREE_KEY ? [] : [worktree.key],
    );
  }
  if (interaction.field === 'gate') {
    return ['auto', 'human', 'conditional'];
  }
  return [];
}

function editorWorkflowFromSnapshot(snapshot: unknown): EditorWorkflow | null {
  if (
    typeof snapshot !== 'object' ||
    snapshot === null ||
    typeof (snapshot as { readonly name?: unknown }).name !== 'string'
  )
    return null;
  try {
    return fromWire(snapshot as WorkflowTemplate);
  } catch {
    return null;
  }
}

function editorIssueFromServer(
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
        )[
          lastPath as keyof {
            depends_on: undefined;
            name: 'name';
            description: 'description';
            mode: 'mode';
            id: 'id';
            title: 'title';
            instructions: 'instructions';
            harness: 'harness';
            model: 'model';
            worktree: 'worktree';
            gate: 'gate';
          }
        ]
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

function WorkflowOutline({
  draft,
  selected,
  issues,
  statuses,
}: {
  readonly draft: EditorWorkflow;
  readonly selected: StageKey | null;
  readonly issues: readonly EditorIssue[];
  readonly statuses: ReadonlyMap<string, string>;
}): JSX.Element {
  const layout = layoutWorkflow(draft);
  return (
    <Box flexDirection="column" flexGrow={1}>
      {layout.ranks.map((rank, rankIndex) => (
        <Box key={`rank:${rank.join('\0')}`} flexDirection="column">
          <Text dimColor>{`Rank ${rankIndex}`}</Text>
          {rank.map((key) => {
            const stage = draft.stages.find((candidate) => candidate.key === key);
            if (stage === undefined) return null;
            const definitionIndex = draft.stages.findIndex((candidate) => candidate.key === key);
            const label = `${stage.key === selected ? '›' : ' '} ${definitionIndex + 1}. ${stage.id || '(blank)'} — ${stage.title || '(untitled)'}${stage.dependsOn.length === 0 ? ' · root' : ` · ← ${stage.dependsOn.join(', ')}`}${statuses.has(stage.id) ? ` · ${statuses.get(stage.id)}` : ''}${issues.some((issue) => issue.stageKey === stage.key) ? ' !' : ''}`;
            return stage.key === selected ? (
              <Text key={stage.key} color="cyan">
                {label}
              </Text>
            ) : (
              <Text key={stage.key}>{label}</Text>
            );
          })}
        </Box>
      ))}
    </Box>
  );
}

function WorkflowInspector({
  stage,
  issues,
  run,
  statuses,
  order,
  total,
  width,
  interaction,
  editorOptions = [],
}: {
  readonly stage: EditorWorkflow['stages'][number] | null;
  readonly issues: readonly EditorIssue[];
  readonly run: WorkflowRun | null;
  readonly statuses: ReadonlyMap<string, string>;
  readonly order: number | undefined;
  readonly total: number;
  readonly width: number;
  readonly interaction?: Extract<Interaction, { readonly kind: 'stage-menu' }>;
  readonly editorOptions?: readonly string[];
}): JSX.Element {
  const theme = useTheme();
  const workflowIssues = issues.filter((issue) => issue.stageKey === undefined);
  const stageIssues =
    stage === null || stage === undefined
      ? workflowIssues
      : [...workflowIssues, ...issues.filter((issue) => issue.stageKey === stage.key)];
  const stageMenu = stage?.key === interaction?.target ? interaction : undefined;
  const row = (field: EditableField, label: string, value: string): JSX.Element => {
    const active = stageMenu?.field === field;
    const shown = active && stageMenu.editing ? `${stageMenu.value}█` : value;
    const text = `${active ? '› ' : '  '}${label}: ${shown}`;
    return active ? (
      <Text key={field} color={theme.accent} bold>
        {text}
      </Text>
    ) : (
      <Text key={field}>{text}</Text>
    );
  };
  return (
    <Box width={width} flexDirection="column" borderStyle="single" paddingX={1}>
      <Text bold>{stageMenu === undefined ? 'Inspector' : 'Stage editor'}</Text>
      {stage === null || stage === undefined ? (
        <Text dimColor>Select a stage</Text>
      ) : (
        <>
          {row('title', 'Name', stage.title || '(untitled)')}
          {row('instructions', 'Description', stage.instructions || '—')}
          {row('harness', 'Harness', stage.harness ?? '(required)')}
          {row('model', 'Model', stage.model ?? '(required)')}
          {row('worktree', 'Worktree', stage.worktree ?? '—')}
          {row('gate', 'Gate', stage.gate)}
          {row('id', 'ID', stage.id || '(blank)')}
          <Text>{`Depends on: ${stage.dependsOn.join(', ') || '—'}`}</Text>
          <Text>{`Definition order: ${order ?? 0} of ${total}`}</Text>
          <Text>{`Runtime: ${statuses.get(stage.id) ?? 'not running'}`}</Text>
          {stageMenu?.editing === true && editorOptions.length > 0 ? (
            <Text dimColor>{`Options (↑/↓): ${formatOptionList(editorOptions, stageMenu.value)}`}</Text>
          ) : null}
        </>
      )}
      {run !== null ? (
        <Text color="green">{`Run ${run.status} · revision ${run.revision}`}</Text>
      ) : null}
      {stageIssues.map((issue) => (
        <Text
          key={`${issue.code}:${issue.stageKey ?? 'workflow'}:${issue.dependencyIndex ?? ''}:${issue.field ?? ''}:${issue.message}`}
          color={issue.severity === 'error' ? 'red' : 'yellow'}
        >{`! ${issue.message}`}</Text>
      ))}
    </Box>
  );
}
