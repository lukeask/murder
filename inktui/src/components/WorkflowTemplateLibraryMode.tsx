/**
 * Full-screen, store-independent workflow-template library.
 *
 * This surface deliberately receives its registry and verbs as values.  The app owns how a
 * template is opened, compiled, saved, or deleted; this mode owns only library navigation,
 * filtering, built-in protection, and the copy-name policy.  Keeping that boundary here makes the
 * library usable by the normal application and small Ink tests without a bus or a Zustand store.
 */

import { useOnClick } from '@ink-tools/ink-mouse';
import type { DOMElement, Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useRef } from 'react';
import type { Mode, ModeHint, ModeStoreApi } from '../input/modeStore.js';
import '../input/dispatcher.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import type { WorkflowTemplate } from '../store/workflows/workflowsSlice.js';
import { useTheme } from '../theme/themeStore.js';
import { Pane } from './Pane.js';

export const WORKFLOW_TEMPLATE_LIBRARY_MODE_ID = 'workflow-template-library';

/** Actions supplied by the caller.  They are intentionally UI-level actions, not RPC-shaped calls. */
export interface WorkflowTemplateLibraryActions {
  /** Open the shared launch-review surface for this saved template. */
  run(workflow: WorkflowTemplate): void;
  /** Open the graph editor with an explicit blank draft. */
  newWorkflowTemplate(): void;
  /** Open the graph editor from a detached, deep-cloned definition. */
  copy(workflow: WorkflowTemplate): void;
  /** Open an owned definition for in-place editing. Never called for built-ins. */
  edit(workflow: WorkflowTemplate): void;
  /** Delete an owned definition with the caller's revision-aware action. */
  delete(workflow: WorkflowTemplate): void;
}

export interface WorkflowTemplateLibraryModeOptions {
  /** A snapshot of the global registry, normally refreshed by the app when it changes. */
  readonly workflows: readonly WorkflowTemplate[];
  readonly actions: WorkflowTemplateLibraryActions;
  /** Select this exact name when opening from `:workflows "Name"`. */
  readonly focusedName?: string | null;
  readonly onDismiss?: () => void;
}

type LibraryIntent =
  | 'up'
  | 'down'
  | 'enter'
  | 'run'
  | 'new'
  | 'copy'
  | 'edit'
  | 'delete'
  | 'escape'
  | 'filter'
  | 'backspace'
  | 'deleteAll';

type Interaction =
  | { readonly kind: 'browse' }
  | { readonly kind: 'filter' }
  | {
      readonly kind: 'confirmDelete';
      readonly workflow: WorkflowTemplate;
    };

interface LibraryState {
  workflows: readonly WorkflowTemplate[];
  actions: WorkflowTemplateLibraryActions;
  cursor: number;
  filter: string;
  interaction: Interaction;
  notice: string | null;
}

/** The canonical sort used in both sections. Names are exact/case-sensitive for lookup, but a
 * case-insensitive order is much easier to scan. */
export function sortWorkflowTemplates(
  workflows: readonly WorkflowTemplate[],
): readonly WorkflowTemplate[] {
  return [...workflows].sort((a, b) => a.name.localeCompare(b.name));
}

/** Split the registry without treating a missing `builtin` value as read-only. */
export function partitionWorkflowTemplates(workflows: readonly WorkflowTemplate[]): {
  readonly mine: readonly WorkflowTemplate[];
  readonly builtIn: readonly WorkflowTemplate[];
} {
  return {
    mine: sortWorkflowTemplates(workflows.filter((workflow) => workflow.builtin !== true)),
    builtIn: sortWorkflowTemplates(workflows.filter((workflow) => workflow.builtin === true)),
  };
}

/** Name matching is intentionally forgiving; execution remains exact-name only. */
export function filterWorkflowTemplates(
  workflows: readonly WorkflowTemplate[],
  filter: string,
): readonly WorkflowTemplate[] {
  const query = filter.trim().toLocaleLowerCase();
  if (query.length === 0) return workflows;
  return workflows.filter((workflow) => workflow.name.toLocaleLowerCase().includes(query));
}

/** The copy policy is shared by mouse and keyboard paths and is exported for callers/tests. */
export function copiedWorkflowName(oldName: string, existingNames: ReadonlySet<string>): string {
  const base = `Copy of ${oldName}`;
  if (!existingNames.has(base)) return base;
  let number = 2;
  while (existingNames.has(`${base} ${number}`)) number += 1;
  return `${base} ${number}`;
}

/**
 * Make a detached definition suitable for the editor's create flow. `structuredClone` matters here:
 * stage/input objects must not share identity with a saved built-in definition.
 */
export function copyWorkflowTemplate(
  workflow: WorkflowTemplate,
  existingNames: ReadonlySet<string>,
): WorkflowTemplate {
  const copy = structuredClone(workflow);
  return {
    ...copy,
    name: copiedWorkflowName(workflow.name, existingNames),
    builtin: false,
    definition_version: 1,
  };
}

function visibleWorkflows(s: LibraryState): readonly WorkflowTemplate[] {
  return filterWorkflowTemplates(sortWorkflowTemplates(s.workflows), s.filter);
}

/**
 * Build the full-screen library mode.  It intentionally has no `AppStoreApi` parameter: pass a new
 * snapshot when reopening after a mutation, while action callbacks bridge to the application's
 * store/editor/launch-review machinery.
 */
export function workflowTemplateLibraryMode(
  modes: ModeStoreApi,
  options: WorkflowTemplateLibraryModeOptions,
): Mode<LibraryIntent> {
  const id = WORKFLOW_TEMPLATE_LIBRARY_MODE_ID;
  const s: LibraryState = {
    workflows: options.workflows,
    actions: options.actions,
    cursor: Math.max(
      0,
      sortWorkflowTemplates(options.workflows).findIndex(
        (workflow) => workflow.name === options.focusedName,
      ),
    ),
    filter: '',
    interaction: { kind: 'browse' },
    notice: null,
  };

  function refresh(): void {
    const frame = modes.getState().stack.find((item) => item.mode.id === id);
    if (frame !== undefined) modes.getState().enter(frame.mode);
  }

  function clampCursor(): void {
    const items = visibleWorkflows(s);
    s.cursor = items.length === 0 ? 0 : Math.max(0, Math.min(s.cursor, items.length - 1));
  }

  function selected(): WorkflowTemplate | null {
    return visibleWorkflows(s)[s.cursor] ?? null;
  }

  function selectWorkflow(workflow: WorkflowTemplate): void {
    const index = visibleWorkflows(s).findIndex((item) => item.name === workflow.name);
    if (index >= 0) {
      s.cursor = index;
      s.notice = null;
      refresh();
    }
  }

  function move(delta: number): void {
    if (s.interaction.kind !== 'browse') return;
    const items = visibleWorkflows(s);
    if (items.length === 0) return;
    s.cursor = (s.cursor + delta + items.length) % items.length;
    s.notice = null;
    refresh();
  }

  function run(): void {
    const workflow = selected();
    if (workflow === null || s.interaction.kind !== 'browse') return;
    s.actions.run(workflow);
  }

  function copy(): void {
    const workflow = selected();
    if (workflow === null || s.interaction.kind !== 'browse') return;
    s.actions.copy(copyWorkflowTemplate(workflow, new Set(s.workflows.map((item) => item.name))));
  }

  function edit(): void {
    const workflow = selected();
    if (workflow === null || s.interaction.kind !== 'browse') return;
    if (workflow.builtin === true) {
      s.notice = 'Built-in workflow templates are read-only. Copy one to customize it.';
      refresh();
      return;
    }
    s.actions.edit(workflow);
  }

  function beginDelete(): void {
    const workflow = selected();
    if (workflow === null || s.interaction.kind !== 'browse') return;
    if (workflow.builtin === true) {
      s.notice = 'Built-in workflow templates are read-only.';
      refresh();
      return;
    }
    s.interaction = { kind: 'confirmDelete', workflow };
    s.notice = null;
    refresh();
  }

  function resolveDelete(confirmed: boolean): void {
    if (s.interaction.kind !== 'confirmDelete') return;
    const workflow = s.interaction.workflow;
    s.interaction = { kind: 'browse' };
    if (confirmed) {
      s.actions.delete(workflow);
      s.notice = `Deleting ${workflow.name}…`;
    }
    refresh();
  }

  function beginFilter(): void {
    if (s.interaction.kind !== 'browse') return;
    s.interaction = { kind: 'filter' };
    s.notice = null;
    refresh();
  }

  function endFilter(): void {
    if (s.interaction.kind !== 'filter') return;
    s.interaction = { kind: 'browse' };
    clampCursor();
    refresh();
  }

  function dismiss(): void {
    modes.getState().exit(id);
    options.onDismiss?.();
  }

  return {
    id,
    presentation: 'fullscreen',
    get hints(): readonly ModeHint[] {
      if (s.interaction.kind === 'filter') {
        return [
          { key: 'type', description: 'filter names' },
          { key: 'enter / esc', description: 'done' },
          { key: 'backspace', description: 'erase' },
        ];
      }
      if (s.interaction.kind === 'confirmDelete') {
        return [
          { key: 'y', description: 'delete' },
          { key: 'n / esc', description: 'cancel' },
        ];
      }
      return [
        { key: 'j/k', description: 'select' },
        { key: 'enter / r', description: 'run' },
        { key: 'n', description: 'new workflow template' },
        { key: 'c', description: 'copy' },
        { key: 'e', description: 'edit' },
        { key: 'd', description: 'delete' },
        { key: '/', description: 'filter' },
        { key: 'esc', description: 'close' },
      ];
    },
    keymap: [
      { chord: { key: { upArrow: true } }, intent: 'up', description: 'previous' },
      { chord: { key: { downArrow: true } }, intent: 'down', description: 'next' },
      { chord: { key: { return: true } }, intent: 'enter', description: 'run' },
      { chord: { key: { escape: true } }, intent: 'escape', description: 'cancel / close' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'erase filter' },
      {
        chord: { input: 'u', key: { meta: true } },
        intent: 'deleteAll',
        description: 'clear filter',
      },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'up':
          move(-1);
          return;
        case 'down':
          move(1);
          return;
        case 'enter':
        case 'run':
          if (s.interaction.kind === 'filter') endFilter();
          else run();
          return;
        case 'new':
          if (s.interaction.kind === 'browse') s.actions.newWorkflowTemplate();
          return;
        case 'copy':
          copy();
          return;
        case 'edit':
          edit();
          return;
        case 'delete':
          beginDelete();
          return;
        case 'filter':
          beginFilter();
          return;
        case 'escape':
          if (s.interaction.kind === 'confirmDelete') resolveDelete(false);
          else if (s.interaction.kind === 'filter') endFilter();
          else dismiss();
          return;
        case 'backspace':
          if (s.interaction.kind === 'filter' && s.filter.length > 0) {
            s.filter = s.filter.slice(0, -1);
            clampCursor();
            refresh();
          }
          return;
        case 'deleteAll':
          if (s.interaction.kind === 'filter') {
            s.filter = '';
            clampCursor();
            refresh();
          }
          return;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input: string, key: Key): boolean {
      if (s.interaction.kind === 'confirmDelete') {
        if (input === 'y' || input === 'Y') {
          resolveDelete(true);
          return true;
        }
        if (input === 'n' || input === 'N') {
          resolveDelete(false);
          return true;
        }
        return false;
      }
      if (s.interaction.kind === 'filter') {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) return false;
        s.filter += input;
        clampCursor();
        refresh();
        return true;
      }
      if (input === 'j') {
        move(1);
        return true;
      }
      if (input === 'k') {
        move(-1);
        return true;
      }
      if (input === 'r') {
        run();
        return true;
      }
      if (input === 'n') {
        s.actions.newWorkflowTemplate();
        return true;
      }
      if (input === 'c') {
        copy();
        return true;
      }
      if (input === 'e') {
        edit();
        return true;
      }
      if (input === 'd') {
        beginDelete();
        return true;
      }
      if (input === '/') {
        beginFilter();
        return true;
      }
      return false;
    },
    render: () => <WorkflowTemplateLibrarySurface state={s} onSelect={selectWorkflow} />,
  };
}

function TemplateRow({
  workflow,
  selected,
  onSelect,
}: {
  readonly workflow: WorkflowTemplate;
  readonly selected: boolean;
  readonly onSelect: (workflow: WorkflowTemplate) => void;
}): JSX.Element {
  const theme = useTheme();
  const ref = useRef<DOMElement>(null);
  useOnClick(ref, (event) => {
    if (event.button === 'left') onSelect(workflow);
  });
  return (
    <Box
      ref={ref}
      flexShrink={0}
      width="100%"
      backgroundColor={selected ? theme.rowSelectedBg : undefined}
    >
      <Text color={selected ? theme.accent : theme.text} bold={selected} wrap="truncate-end">
        {`${selected ? '▌' : ' '} ${workflow.name}`}
      </Text>
    </Box>
  );
}

function WorkflowTemplateLibrarySurface({
  state: s,
  onSelect,
}: {
  readonly state: LibraryState;
  readonly onSelect: (workflow: WorkflowTemplate) => void;
}): JSX.Element {
  const theme = useTheme();
  const { columns } = useTerminalSize();
  const workflows = visibleWorkflows(s);
  const { mine, builtIn } = partitionWorkflowTemplates(workflows);
  const selected = selectedWorkflowForDisplay(s, workflows);
  const listWidth = Math.max(30, Math.min(44, Math.floor(columns * 0.4)));
  const detailWidth = Math.max(30, columns - listWidth);
  const deleting = s.interaction.kind === 'confirmDelete' ? s.interaction.workflow : null;

  return (
    <Box width="100%" height="100%" flexDirection="column" overflow="hidden">
      <Pane
        title="Workflow template library"
        titleExtra={<Text color={theme.muted}>{`  ${s.workflows.length} saved`}</Text>}
        focused
        footerLeft={
          deleting !== null ? (
            <Text color={theme.warning} wrap="truncate-end">
              {`Delete “${deleting.name}”? y confirms · n / esc keeps it`}
            </Text>
          ) : s.interaction.kind === 'filter' ? (
            <Text
              color={theme.muted}
              wrap="truncate-end"
            >{`Filter: ${s.filter || '(type a name)'}`}</Text>
          ) : s.notice === null ? (
            <Text color={theme.muted}>
              Enter opens launch review · built-ins can be run or copied
            </Text>
          ) : (
            <Text color={theme.muted} wrap="truncate-end">
              {s.notice}
            </Text>
          )
        }
      >
        <Box flexDirection="row" flexGrow={1} minHeight={0} overflow="hidden">
          <Box width={listWidth} flexShrink={0} flexDirection="column" overflow="hidden">
            <Pane
              title="Workflow templates"
              focused={s.interaction.kind === 'browse' || s.interaction.kind === 'filter'}
            >
              {s.interaction.kind === 'filter' ? (
                <Text color={theme.accent} wrap="truncate-end">{`▌ Filter: ${s.filter}`}</Text>
              ) : null}
              <Text color={theme.muted} bold>
                My workflow templates
              </Text>
              {mine.length === 0 ? <Text color={theme.muted}> none</Text> : null}
              {mine.map((workflow) => (
                <TemplateRow
                  key={workflow.name}
                  workflow={workflow}
                  selected={selected?.name === workflow.name && s.interaction.kind === 'browse'}
                  onSelect={onSelect}
                />
              ))}
              <Box height={1} flexShrink={0} />
              <Text color={theme.muted} bold>
                Built-in workflow templates
              </Text>
              {builtIn.length === 0 ? <Text color={theme.muted}> none</Text> : null}
              {builtIn.map((workflow) => (
                <TemplateRow
                  key={workflow.name}
                  workflow={workflow}
                  selected={selected?.name === workflow.name && s.interaction.kind === 'browse'}
                  onSelect={onSelect}
                />
              ))}
              {workflows.length === 0 ? (
                <Text color={theme.muted}>No matching workflow templates.</Text>
              ) : null}
            </Pane>
          </Box>
          <Box
            width={detailWidth}
            flexGrow={1}
            minWidth={0}
            flexDirection="column"
            overflow="hidden"
          >
            <Pane
              title={deleting !== null ? 'Confirm deletion' : 'Template details'}
              focused={deleting !== null}
            >
              {deleting !== null ? (
                <>
                  <Text
                    color={theme.warning}
                    bold
                    wrap="truncate-end"
                  >{`Delete “${deleting.name}”?`}</Text>
                  <Text color={theme.muted}>
                    This cannot be undone. The current registry revision will be checked.
                  </Text>
                </>
              ) : selected === null ? (
                <Text color={theme.muted}>Select a workflow template to inspect it.</Text>
              ) : (
                <WorkflowTemplateDetails workflow={selected} />
              )}
            </Pane>
          </Box>
        </Box>
      </Pane>
    </Box>
  );
}

function selectedWorkflowForDisplay(
  state: LibraryState,
  workflows: readonly WorkflowTemplate[],
): WorkflowTemplate | null {
  if (state.interaction.kind === 'confirmDelete') return state.interaction.workflow;
  return workflows[state.cursor] ?? null;
}

function WorkflowTemplateDetails({
  workflow,
}: {
  readonly workflow: WorkflowTemplate;
}): JSX.Element {
  const theme = useTheme();
  const inputs = Object.keys(workflow.inputs ?? {});
  const stageCount = workflow.stages?.length ?? 0;
  const row = (label: string, value: string, color = theme.text): JSX.Element => (
    <Box key={label} flexDirection="row" flexShrink={0}>
      <Text color={theme.muted}>{`${label.padEnd(9)} `}</Text>
      <Text color={color} wrap="truncate-end">
        {value}
      </Text>
    </Box>
  );
  return (
    <Box flexDirection="column">
      <Text bold color={theme.text} wrap="truncate-end">
        {workflow.name}
      </Text>
      <Box height={1} flexShrink={0} />
      {row(
        'Description',
        workflow.description?.trim() || 'No description',
        workflow.description ? theme.text : theme.muted,
      )}
      {row('Stages', String(stageCount))}
      {row('Inputs', inputs.length === 0 ? 'none' : `${inputs.length}: ${inputs.join(', ')}`)}
      {row('Mode', workflow.mode ?? 'static')}
      {row(
        'Status',
        workflow.builtin === true ? 'Built-in · read-only' : 'My template',
        workflow.builtin === true ? theme.warning : theme.text,
      )}
      <Box height={1} flexShrink={0} />
      <Text color={theme.muted} wrap="truncate-end">
        {workflow.builtin === true
          ? 'Enter/r runs this template; c copies it into an editable draft.'
          : 'Enter/r runs; e edits; c makes a detached copy; d deletes.'}
      </Text>
    </Box>
  );
}
