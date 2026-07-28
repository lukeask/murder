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
} from '../store/dialogs/harnessModelsActions.js';
import {
  MAIN_WORKTREE_KEY,
  NEW_WORKTREE_KEY,
  type WorktreeOption,
  type WorktreeOptionsActions,
} from '../store/dialogs/worktreeOptionsActions.js';
import type { AppStoreApi } from '../store/store.js';
import type { WorkflowRun } from '../store/workflowRuns/workflowRunsSlice.js';
import type { WorkflowDef } from '../store/workflows/workflowsSlice.js';
import { useTheme } from '../theme/themeStore.js';
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
import { collectPlaceholders } from '../workflowEditor/placeholders.js';
import {
  applyWorkflowEdit,
  dependencyLegality,
  type HistoryEntry,
  type WorkflowEdit,
} from '../workflowEditor/reducer.js';
import { decodeStaticDagStatuses } from '../workflowEditor/runState.js';
import { validateEditorWorkflow } from '../workflowEditor/validate.js';
import { fromWire, toWire } from '../workflowEditor/wire.js';
import { TextRuns } from './TextRuns.js';

export const WORKFLOW_EDITOR_MODE_ID = 'workflow-editor';

type Interaction =
  | { readonly kind: 'normal' }
  | { readonly kind: 'connect'; readonly candidate: StageKey | null }
  | {
      readonly kind: 'edit';
      readonly target: StageKey | 'workflow';
      readonly field: EditableField;
      readonly value: string;
    }
  | {
      readonly kind: 'run-args';
      readonly names: readonly string[];
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

export interface WorkflowEditorModeOptions {
  readonly workflow?: WorkflowDef;
  /** Called after a successfully saved definition, useful to focus a caller-owned list. */
  readonly onSaved?: (workflow: WorkflowDef) => void;
  readonly harnessModels?: HarnessModelsActions;
  readonly worktreeOptions?: WorktreeOptionsActions;
}

type WorkflowEditorIntent =
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

/** Full-screen local-draft workflow editor. The canonical slice is written only by workflow.put. */
export function workflowEditorMode(
  modes: ModeStoreApi,
  app: AppStoreApi,
  options: WorkflowEditorModeOptions = {},
): Mode<WorkflowEditorIntent> {
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
    harnessModels: {},
    worktrees: [],
  };
  const id = WORKFLOW_EDITOR_MODE_ID;

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
    s.selected = issue.stageKey;
    const rect = layoutWorkflow(s.draft).nodes.get(issue.stageKey)?.rect;
    if (rect !== undefined) {
      s.viewport = autoPan(s.viewport, rect, s.canvasWidth, s.canvasHeight);
    }
  }
  function startEdit(): void {
    const target = s.selected ?? 'workflow';
    const stage = target === 'workflow' ? null : s.draft.stages.find((item) => item.key === target);
    const field: EditableField = stage === null || stage === undefined ? 'name' : 'title';
    const value = stage === null || stage === undefined ? s.draft.name : stage.title;
    s.interaction = { kind: 'edit', target, field, value };
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
    s.interaction = { ...interaction, field, value };
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
      void app.getState().actions.workflows.run(s.draft.name, args);
      s.interaction = { kind: 'normal' };
      refresh();
    };
    const ask = (): void => {
      const names = collectPlaceholders(s.draft);
      if (names.length === 0) {
        runWith({});
        return;
      }
      s.interaction = {
        kind: 'run-args',
        names,
        values: Object.fromEntries(names.map((name) => [name, ''])),
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
      return hints(s.interaction);
    },
    get keymap(): Keymap<WorkflowEditorIntent> {
      return workflowEditorKeymap(s.interaction);
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
      if (s.interaction.kind === 'edit') {
        if (intent === 'enter') {
          apply({
            type: 'set-field',
            key: s.interaction.target,
            field: s.interaction.field,
            value: s.interaction.value,
          });
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'escape') {
          s.interaction = { kind: 'normal' };
          refresh();
        } else if (intent === 'backspace') {
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
      if (s.interaction.kind === 'run-args') {
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
            cursor: (s.interaction.cursor + 1) % s.interaction.names.length,
          };
          refresh();
        } else if (intent === 'argsPrev') {
          s.interaction = {
            ...s.interaction,
            cursor:
              (s.interaction.cursor - 1 + s.interaction.names.length) % s.interaction.names.length,
          };
          refresh();
        } else if (intent === 'backspace') {
          const name = s.interaction.names[s.interaction.cursor];
          if (name === undefined) return;
          s.interaction.values[name] = (s.interaction.values[name] ?? '').slice(0, -1);
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
      } else if (intent === 'enter') startEdit();
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
      else if (intent === 'inspector') {
        s.inspectorOpen = !s.inspectorOpen;
        refresh();
      } else if (intent === 'search') {
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
        s.interaction = { ...s.interaction, value: s.interaction.value + input };
        refresh();
        return true;
      }
      if (s.interaction.kind === 'run-args') {
        const name = s.interaction.names[s.interaction.cursor];
        if (name === undefined) return false;
        s.interaction.values[name] = (s.interaction.values[name] ?? '') + input;
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
      <WorkflowEditorSurface
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

function workflowEditorKeymap(interaction: Interaction): Keymap<WorkflowEditorIntent> {
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
  if (interaction.kind === 'search' || interaction.kind === 'run-args') {
    return [escapeBinding, enter, backspace, tab, backtab];
  }
  if (interaction.kind === 'edit') {
    return [
      escapeBinding,
      enter,
      backspace,
      tab,
      backtab,
      { chord: { key: { upArrow: true } }, intent: 'up', description: 'previous option' },
      { chord: { key: { downArrow: true } }, intent: 'down', description: 'next option' },
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
  const navigation: Keymap<WorkflowEditorIntent> = [
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
    { chord: { input: 'i' }, intent: 'inspector', description: 'inspector' },
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

function hints(interaction: Interaction): readonly ModeHint[] {
  if (interaction.kind === 'edit')
    return [
      { key: 'enter', description: 'commit field' },
      { key: 'esc', description: 'cancel' },
    ];
  if (interaction.kind === 'connect')
    return [
      { key: 'j/k/h/l', description: 'candidate' },
      { key: 'enter', description: 'toggle dependency' },
      { key: 'esc', description: 'done' },
    ];
  if (interaction.kind === 'run-args')
    return [
      { key: 'tab', description: 'next arg' },
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
    { key: 'a', description: 'add' },
    { key: 'c', description: 'dependencies' },
    { key: 's', description: 'save' },
    { key: 'R', description: 'run' },
    { key: 'esc', description: 'close' },
  ];
}

function WorkflowEditorSurface({
  session,
  onSelect,
  onScroll,
}: {
  readonly session: Session;
  readonly onSelect: (key: StageKey) => void;
  readonly onScroll: (delta: number) => void;
}): JSX.Element {
  const { columns, rows } = useTerminalSize();
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
  const canvasHeight = Math.max(5, rows - 4);
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
  const runArgs = session.interaction.kind === 'run-args' ? session.interaction : null;
  const editOptions =
    session.interaction.kind === 'edit' ? optionsForEditorField(session, session.interaction) : [];
  const editValue = session.interaction.kind === 'edit' ? session.interaction.value : '';
  return (
    <Box width={columns} height={rows} flexDirection="column" overflow="hidden">
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
            >{`Options (↑/↓): ${editOptions.map((option) => (option === editValue ? `[${option}]` : option)).join(' · ')}`}</Text>
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
      {runArgs !== null ? (
        <Text
          color={theme.accent}
        >{`Run arguments  ${runArgs.names.map((name, index) => `${index === runArgs.cursor ? '[' : ''}${name}=${runArgs.values[name]}${index === runArgs.cursor ? ']' : ''}`).join('  ')}`}</Text>
      ) : null}
      {runSnapshot !== null ? (
        <Text
          dimColor
        >{`Monitoring immutable run snapshot v${run?.definition_version ?? '?'}`}</Text>
      ) : null}
      {narrow ? (
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

function optionsForEditorField(
  session: Session,
  interaction: Extract<Interaction, { readonly kind: 'edit' }>,
): readonly string[] {
  if (interaction.target === 'workflow') {
    return interaction.field === 'mode' ? ['static', 'generative'] : [];
  }
  const stage = session.draft.stages.find((candidate) => candidate.key === interaction.target);
  if (interaction.field === 'harness') {
    return Object.keys(session.harnessModels).sort();
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
    return fromWire(snapshot as WorkflowDef);
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
}: {
  readonly stage: EditorWorkflow['stages'][number] | null;
  readonly issues: readonly EditorIssue[];
  readonly run: WorkflowRun | null;
  readonly statuses: ReadonlyMap<string, string>;
  readonly order: number | undefined;
  readonly total: number;
  readonly width: number;
}): JSX.Element {
  const workflowIssues = issues.filter((issue) => issue.stageKey === undefined);
  const stageIssues =
    stage === null || stage === undefined
      ? workflowIssues
      : [...workflowIssues, ...issues.filter((issue) => issue.stageKey === stage.key)];
  return (
    <Box width={width} flexDirection="column" borderStyle="single" paddingX={1}>
      <Text bold>Inspector</Text>
      {stage === null || stage === undefined ? (
        <Text dimColor>Select a stage</Text>
      ) : (
        <>
          <Text>{`ID: ${stage.id || '(blank)'}`}</Text>
          <Text>{`Title: ${stage.title || '(blank)'}`}</Text>
          <Text>{`Harness: ${stage.harness ?? '(required)'}`}</Text>
          <Text>{`Model: ${stage.model ?? '(required)'}`}</Text>
          <Text>{`Worktree: ${stage.worktree ?? '—'}`}</Text>
          <Text>{`Gate: ${stage.gate}`}</Text>
          <Text>{`Depends on: ${stage.dependsOn.join(', ') || '—'}`}</Text>
          <Text>{`Definition order: ${order ?? 0} of ${total}`}</Text>
          <Text>{`Runtime: ${statuses.get(stage.id) ?? 'not running'}`}</Text>
          <Text dimColor>{stage.instructions || '(no instructions)'}</Text>
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
