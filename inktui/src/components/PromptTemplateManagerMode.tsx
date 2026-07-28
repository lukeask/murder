/**
 * `PromptTemplateManagerMode` — dedicated modal for prompt-template CRUD (list / create / edit /
 * rename / delete) plus preview of `{inputs}`, inline `:refs:`, workflow usages, and a single-pass
 * expansion preview. Extracted from the Templates section of {@link ./SettingsModal.js}.
 *
 * Persistence stays on the templates store actions (`save` / `rename` / `remove`); this mode never
 * talks to the bus directly.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useEffect } from 'react';
import { shallow } from 'zustand/shallow';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import { useModalHeight, useModalWidth } from '../hooks/useTerminalSize.js';
import type { Mode, ModeHint, ModeStoreApi } from '../input/modeStore.js';
import '../input/dispatcher.js';
import { applyEditorKey } from '../input/textEditor/applyEditorKey.js';
import type { EditorCommand } from '../input/textEditor/commands.js';
import {
  multilineEditorPolicy,
  singleLineEditorPolicy,
} from '../input/textEditor/keyDecoder.js';
import { reduceEditor } from '../input/textEditor/operations.js';
import { plainTextProjection } from '../input/textEditor/projection.js';
import { editorAtEnd, type TextEditorState } from '../input/textEditor/state.js';
import { plainTextTopology } from '../input/textEditor/topology.js';
import type { AppStore, AppStoreApi } from '../store/store.js';
import type { TemplateRecord } from '../store/templates/templatesSlice.js';
import type { WorkflowTemplate } from '../store/workflows/workflowsSlice.js';
import { useTheme } from '../theme/themeStore.js';
import {
  collectBodyPlaceholders,
  collectUnknownInlineRefs,
  expandInlinePreview,
  findWorkflowReferences,
  formatWorkflowTemplateRef,
  previewBodyFlat,
  validateTemplateName,
  type WorkflowTemplateRef,
} from './promptTemplates/refs.js';
import { TextEditorDisplay } from './TextEditorDisplay.js';

export const PROMPT_TEMPLATE_MANAGER_MODE_ID = 'prompt-template-manager';

const LIST_EDITOR_WIDTH = 36;
const BODY_EDITOR_WIDTH = 64;

export interface PromptTemplateActions {
  remove(name: string): void;
  rename(oldName: string, newName: string): void;
  save(name: string, body: string): void;
}

export interface PromptTemplateManagerModeOptions {
  readonly onDismiss?: () => void;
  /** Seed / override list (tests). When omitted, the store snapshot is used. */
  readonly templates?: readonly TemplateRecord[];
  readonly templateActions?: PromptTemplateActions;
  /** Workflow templates used for referential warnings (tests / callers without store). */
  readonly workflows?: readonly WorkflowTemplate[];
}

type ListRow =
  | { readonly kind: 'create' }
  | { readonly kind: 'template'; readonly name: string };

type Interaction =
  | { readonly kind: 'normal' }
  | { readonly kind: 'createName' }
  | { readonly kind: 'editBody'; readonly name: string; readonly isNew: boolean }
  | { readonly kind: 'rename'; readonly name: string }
  | {
      readonly kind: 'confirmDelete';
      readonly name: string;
      readonly refs: readonly WorkflowTemplateRef[];
    }
  | {
      readonly kind: 'confirmRename';
      readonly oldName: string;
      readonly newName: string;
      readonly refs: readonly WorkflowTemplateRef[];
    };

type Intent =
  | 'up'
  | 'down'
  | 'enter'
  | 'escape'
  | 'backspace'
  | 'deleteForward'
  | 'home'
  | 'end'
  | 'deleteAll'
  | 'newline'
  | 'left'
  | 'right';

interface ManagerState {
  templates: readonly TemplateRecord[];
  workflows: readonly WorkflowTemplate[];
  actions: PromptTemplateActions;
  cursor: number;
  interaction: Interaction;
  editEditor: TextEditorState;
  notice: string | null;
}

function buildRows(templates: readonly TemplateRecord[]): readonly ListRow[] {
  return [{ kind: 'create' }, ...templates.map((t) => ({ kind: 'template' as const, name: t.name }))];
}

function workflowNameSet(workflows: readonly WorkflowTemplate[]): ReadonlySet<string> {
  return new Set(workflows.map((w) => w.name));
}

function refsWillKeepOldNameNotice(
  oldName: string,
  newName: string,
  refs: readonly WorkflowTemplateRef[],
): string {
  return `Rename :${oldName}: → :${newName}:? ${refs.length} workflow ref(s) will keep :${oldName}: (y/n)`;
}

/**
 * Build the prompt-template manager {@link Mode}. Prefer passing an {@link AppStoreApi} so the
 * surface tracks live registry edits; tests may seed via {@link PromptTemplateManagerModeOptions}.
 */
export function promptTemplateManagerMode(
  modes: ModeStoreApi,
  store: AppStoreApi | null,
  opts: PromptTemplateManagerModeOptions = {},
): Mode<Intent> {
  const id = PROMPT_TEMPLATE_MANAGER_MODE_ID;

  const initialFromStore = store?.getState();
  const s: ManagerState = {
    templates: opts.templates ?? initialFromStore?.templates.items ?? [],
    workflows: opts.workflows ?? initialFromStore?.workflows.items ?? [],
    actions:
      opts.templateActions ??
      initialFromStore?.actions.templates ?? {
        remove() {},
        rename() {},
        save() {},
      },
    cursor: 0,
    interaction: { kind: 'normal' },
    editEditor: editorAtEnd(),
    notice: null,
  };

  function refresh(): void {
    const frame = modes.getState().stack.find((f) => f.mode.id === id);
    if (frame !== undefined) {
      modes.getState().enter(frame.mode);
    }
  }

  function rows(): readonly ListRow[] {
    return buildRows(s.templates);
  }

  function focusedRow(): ListRow | undefined {
    return rows()[s.cursor];
  }

  function clampCursor(): void {
    const list = rows();
    if (list.length === 0) {
      s.cursor = 0;
      return;
    }
    s.cursor = Math.max(0, Math.min(s.cursor, list.length - 1));
  }

  function setEditValue(text: string): void {
    s.editEditor = editorAtEnd(text);
  }

  function applyEdit(command: EditorCommand): void {
    const multiline = s.interaction.kind === 'editBody';
    s.editEditor = reduceEditor(s.editEditor, command, {
      topology: plainTextTopology,
      projection: plainTextProjection,
      width: multiline ? BODY_EDITOR_WIDTH : LIST_EDITOR_WIDTH,
    }).state;
  }

  function moveCursor(delta: number): void {
    if (s.interaction.kind !== 'normal') return;
    const list = rows();
    if (list.length === 0) return;
    s.cursor = (s.cursor + delta + list.length) % list.length;
    s.notice = null;
    refresh();
  }

  function beginCreate(): void {
    s.interaction = { kind: 'createName' };
    setEditValue('');
    s.notice = 'New template name. Enter to continue, Esc to cancel.';
    refresh();
  }

  function beginRename(name: string): void {
    s.interaction = { kind: 'rename', name };
    setEditValue(name);
    s.notice = 'Rename template. Enter to save, Esc to cancel.';
    refresh();
  }

  function beginEditBody(name: string, isNew: boolean, seed = ''): void {
    s.interaction = { kind: 'editBody', name, isNew };
    setEditValue(seed);
    s.notice = isNew
      ? 'Template body. Enter to save, Shift+Enter for newline, Esc to cancel.'
      : 'Edit body. Enter to save, Shift+Enter for newline, Esc to cancel.';
    refresh();
  }

  function beginDelete(name: string): void {
    const refs = findWorkflowReferences(name, s.workflows);
    s.interaction = { kind: 'confirmDelete', name, refs };
    s.notice =
      refs.length === 0
        ? `delete "${name}"? (y/n)`
        : `delete "${name}"? referenced by ${refs.length} workflow field(s) — will break those refs (y/n)`;
    refresh();
  }

  function commitCreateName(): void {
    if (s.interaction.kind !== 'createName') return;
    const name = s.editEditor.text.trim();
    const error = validateTemplateName(name, null, s.templates);
    if (error !== null) {
      s.notice = error;
      refresh();
      return;
    }
    beginEditBody(name, true, '');
  }

  function commitEditBody(): void {
    if (s.interaction.kind !== 'editBody') return;
    const { name, isNew } = s.interaction;
    const body = s.editEditor.text;
    s.actions.save(name, body);
    if (store === null) {
      const without = s.templates.filter((t) => t.name !== name);
      s.templates = [...without, { name, body }].sort((a, b) => a.name.localeCompare(b.name));
    }
    s.interaction = { kind: 'normal' };
    setEditValue('');
    s.notice = isNew ? `Created :${name}:` : `Saved :${name}:`;
    const idx = rows().findIndex((r) => r.kind === 'template' && r.name === name);
    if (idx >= 0) s.cursor = idx;
    refresh();
  }

  function finishRename(oldName: string, newName: string): void {
    s.actions.rename(oldName, newName);
    if (store === null && oldName !== newName) {
      s.templates = s.templates.map((t) => (t.name === oldName ? { ...t, name: newName } : t));
    }
    s.interaction = { kind: 'normal' };
    setEditValue('');
    const refs = findWorkflowReferences(oldName, s.workflows);
    s.notice =
      refs.length > 0
        ? `Renamed :${oldName}: → :${newName}: — ${refs.length} workflow ref(s) still use :${oldName}:`
        : null;
    const idx = rows().findIndex((r) => r.kind === 'template' && r.name === newName);
    if (idx >= 0) s.cursor = idx;
    else clampCursor();
    refresh();
  }

  function commitRename(): void {
    if (s.interaction.kind !== 'rename') return;
    const oldName = s.interaction.name;
    const newName = s.editEditor.text.trim();
    const error = validateTemplateName(newName, oldName, s.templates);
    if (error !== null) {
      s.notice = error;
      refresh();
      return;
    }
    if (newName === oldName) {
      s.interaction = { kind: 'normal' };
      setEditValue('');
      s.notice = null;
      refresh();
      return;
    }
    const refs = findWorkflowReferences(oldName, s.workflows);
    if (refs.length > 0) {
      s.interaction = { kind: 'confirmRename', oldName, newName, refs };
      s.notice = refsWillKeepOldNameNotice(oldName, newName, refs);
      refresh();
      return;
    }
    finishRename(oldName, newName);
  }

  function resolveDelete(confirmed: boolean): void {
    if (s.interaction.kind !== 'confirmDelete') return;
    const { name } = s.interaction;
    s.interaction = { kind: 'normal' };
    s.notice = null;
    if (confirmed) {
      s.actions.remove(name);
      if (store === null) {
        s.templates = s.templates.filter((t) => t.name !== name);
      }
      clampCursor();
    }
    refresh();
  }

  function resolveRenameConfirm(confirmed: boolean): void {
    if (s.interaction.kind !== 'confirmRename') return;
    const { oldName, newName } = s.interaction;
    if (confirmed) {
      finishRename(oldName, newName);
      return;
    }
    s.interaction = { kind: 'normal' };
    setEditValue('');
    s.notice = null;
    refresh();
  }

  function cancelInteraction(): void {
    if (s.interaction.kind === 'normal') {
      modes.getState().exit(id);
      opts.onDismiss?.();
      return;
    }
    s.interaction = { kind: 'normal' };
    setEditValue('');
    s.notice = null;
    refresh();
  }

  function onEnter(): void {
    switch (s.interaction.kind) {
      case 'createName':
        commitCreateName();
        return;
      case 'editBody':
        commitEditBody();
        return;
      case 'rename':
        commitRename();
        return;
      case 'confirmDelete':
      case 'confirmRename':
        return;
      case 'normal': {
        const row = focusedRow();
        if (row === undefined) return;
        if (row.kind === 'create') {
          beginCreate();
          return;
        }
        const template = s.templates.find((t) => t.name === row.name);
        beginEditBody(row.name, false, template?.body ?? '');
        return;
      }
      default:
        return;
    }
  }

  function syncFromStore(
    items: readonly TemplateRecord[],
    workflows: readonly WorkflowTemplate[],
    actions: PromptTemplateActions,
  ): void {
    s.actions = actions;
    const templatesChanged =
      items.length !== s.templates.length ||
      items.some((t, i) => t.name !== s.templates[i]?.name || t.body !== s.templates[i]?.body);
    const workflowsChanged =
      workflows.length !== s.workflows.length ||
      workflows.some((w, i) => w.name !== s.workflows[i]?.name);
    if (!templatesChanged && !workflowsChanged) return;
    s.templates = items;
    s.workflows = workflows;
    clampCursor();
    refresh();
  }

  const mode: Mode<Intent> = {
    id,
    presentation: 'modal',
    get hints(): readonly ModeHint[] {
      if (s.interaction.kind === 'editBody') {
        return [
          { key: 'enter', description: 'save' },
          { key: 'shift+enter', description: 'newline' },
          { key: 'esc', description: 'cancel' },
        ];
      }
      if (s.interaction.kind === 'createName' || s.interaction.kind === 'rename') {
        return [
          { key: 'enter', description: 'confirm' },
          { key: 'esc', description: 'cancel' },
        ];
      }
      if (s.interaction.kind === 'confirmDelete' || s.interaction.kind === 'confirmRename') {
        return [
          { key: 'y', description: 'confirm' },
          { key: 'n', description: 'cancel' },
        ];
      }
      return [
        { key: 'j/k', description: 'nav' },
        { key: 'enter', description: 'edit' },
        { key: 'r', description: 'rename' },
        { key: 'n', description: 'new' },
        { key: 'd', description: 'delete' },
        { key: 'esc', description: 'close' },
      ];
    },
    keymap: [
      { chord: { key: { upArrow: true } }, intent: 'up', description: 'prev' },
      { chord: { key: { downArrow: true } }, intent: 'down', description: 'next' },
      { chord: { key: { leftArrow: true } }, intent: 'left', description: 'left' },
      { chord: { key: { rightArrow: true } }, intent: 'right', description: 'right' },
      // Shift+Enter must precede bare Enter: unlisted shift is don't-care, first-match-wins.
      {
        chord: { key: { shift: true, return: true } },
        intent: 'newline',
        description: 'newline',
      },
      { chord: { key: { return: true } }, intent: 'enter', description: 'confirm' },
      { chord: { key: { escape: true } }, intent: 'escape', description: 'cancel' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { key: { delete: true } }, intent: 'deleteForward', description: 'delete' },
      { chord: { key: { home: true } }, intent: 'home', description: 'line start' },
      { chord: { key: { end: true } }, intent: 'end', description: 'line end' },
      { chord: { input: 'u', key: { meta: true } }, intent: 'deleteAll', description: 'clear' },
    ],
    onIntent(intent) {
      const editing =
        s.interaction.kind === 'createName' ||
        s.interaction.kind === 'editBody' ||
        s.interaction.kind === 'rename';

      switch (intent) {
        case 'up':
          if (editing) {
            applyEdit({ type: 'moveVisualUp' });
            refresh();
          } else moveCursor(-1);
          break;
        case 'down':
          if (editing) {
            applyEdit({ type: 'moveVisualDown' });
            refresh();
          } else moveCursor(1);
          break;
        case 'left':
          if (editing) {
            applyEdit({ type: 'moveLeft' });
            refresh();
          }
          break;
        case 'right':
          if (editing) {
            applyEdit({ type: 'moveRight' });
            refresh();
          }
          break;
        case 'enter':
          onEnter();
          break;
        case 'newline':
          if (s.interaction.kind === 'editBody') {
            applyEdit({ type: 'insertNewline' });
            refresh();
          }
          break;
        case 'escape':
          cancelInteraction();
          break;
        case 'backspace':
          if (editing) {
            applyEdit({ type: 'backspace' });
            refresh();
          }
          break;
        case 'deleteForward':
          if (editing) {
            applyEdit({ type: 'deleteForward' });
            refresh();
          }
          break;
        case 'home':
          if (editing) {
            applyEdit({ type: 'moveLineStart' });
            refresh();
          }
          break;
        case 'end':
          if (editing) {
            applyEdit({ type: 'moveLineEnd' });
            refresh();
          }
          break;
        case 'deleteAll':
          if (editing) {
            setEditValue('');
            refresh();
          }
          break;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input: string, key: Key): boolean {
      const editing =
        s.interaction.kind === 'createName' ||
        s.interaction.kind === 'editBody' ||
        s.interaction.kind === 'rename';

      if (editing) {
        const transition = applyEditorKey(s.editEditor, input, key, {
          policy:
            s.interaction.kind === 'editBody' ? multilineEditorPolicy : singleLineEditorPolicy,
          environment: {
            width: s.interaction.kind === 'editBody' ? BODY_EDITOR_WIDTH : LIST_EDITOR_WIDTH,
            topology: plainTextTopology,
            projection: plainTextProjection,
          },
        });
        if (transition === null) return false;
        s.editEditor = transition.state;
        refresh();
        return true;
      }

      if (s.interaction.kind === 'confirmDelete') {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) return false;
        if (input === 'y' || input === 'Y') resolveDelete(true);
        else if (input === 'n' || input === 'N') resolveDelete(false);
        return true;
      }
      if (s.interaction.kind === 'confirmRename') {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) return false;
        if (input === 'y' || input === 'Y') resolveRenameConfirm(true);
        else if (input === 'n' || input === 'N') resolveRenameConfirm(false);
        return true;
      }

      if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) return false;
      if (input === 'j') {
        moveCursor(1);
        return true;
      }
      if (input === 'k') {
        moveCursor(-1);
        return true;
      }
      if (input === 'n') {
        beginCreate();
        return true;
      }
      if (input === 'r') {
        const row = focusedRow();
        if (row?.kind === 'template') {
          beginRename(row.name);
          return true;
        }
        return false;
      }
      if (input === 'd') {
        const row = focusedRow();
        if (row?.kind === 'template') {
          beginDelete(row.name);
          return true;
        }
        return false;
      }
      return false;
    },
    render: () => (
      <PromptTemplateManagerDialog state={s} store={store} syncFromStore={syncFromStore} />
    ),
  };

  return mode;
}

function PromptTemplateManagerDialog({
  state: s,
  store,
  syncFromStore,
}: {
  readonly state: ManagerState;
  readonly store: AppStoreApi | null;
  readonly syncFromStore: (
    items: readonly TemplateRecord[],
    workflows: readonly WorkflowTemplate[],
    actions: PromptTemplateActions,
  ) => void;
}): JSX.Element {
  const theme = useTheme();
  const width = useModalWidth(78);
  const height = useModalHeight(0.75);

  const live = useStoreWithEqualityFn(
    store ?? EMPTY_STORE,
    (st) => ({
      items: st.templates.items,
      workflows: st.workflows.items,
      actions: st.actions.templates,
    }),
    shallow,
  );

  useEffect(() => {
    if (store === null) return;
    syncFromStore(live.items, live.workflows, live.actions);
  }, [store, live, syncFromStore]);

  const list = buildRows(s.templates);
  const focused = list[s.cursor];
  const selectedName =
    s.interaction.kind === 'editBody'
      ? s.interaction.name
      : s.interaction.kind === 'rename'
        ? s.interaction.name
        : s.interaction.kind === 'confirmDelete'
          ? s.interaction.name
          : s.interaction.kind === 'confirmRename'
            ? s.interaction.oldName
            : focused?.kind === 'template'
              ? focused.name
              : null;
  const selected =
    selectedName === null ? null : (s.templates.find((t) => t.name === selectedName) ?? null);
  const bodyForPreview =
    s.interaction.kind === 'editBody' ? s.editEditor.text : (selected?.body ?? null);
  const knownNames = new Set(s.templates.map((t) => t.name));
  const workflowNames = workflowNameSet(s.workflows);
  const placeholders = bodyForPreview === null ? [] : collectBodyPlaceholders(bodyForPreview);
  const unknownRefs =
    bodyForPreview === null
      ? []
      : collectUnknownInlineRefs(bodyForPreview, knownNames, selectedName ?? undefined);
  const registry = new Map(s.templates.map((t) => [t.name, t.body] as const));
  const expansion =
    bodyForPreview === null ? null : expandInlinePreview(bodyForPreview, registry);
  const workflowRefs =
    selectedName === null ? [] : findWorkflowReferences(selectedName, s.workflows);
  const confirmingRefs =
    s.interaction.kind === 'confirmRename' || s.interaction.kind === 'confirmDelete'
      ? s.interaction.refs
      : null;

  const editingName =
    s.interaction.kind === 'createName' || s.interaction.kind === 'rename';
  const editingBody = s.interaction.kind === 'editBody';

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      width={width}
      height={height}
      paddingX={1}
      paddingY={0}
    >
      <Box flexShrink={0} marginBottom={1}>
        <Text bold color={theme.accent}>
          Prompt Templates
        </Text>
        <Text color={theme.muted}>{'  '}:name: macros</Text>
      </Box>

      <Box flexDirection="row" flexGrow={1} gap={1}>
        <Box flexDirection="column" width={Math.min(32, Math.floor(width * 0.38))} flexShrink={0}>
          {list.map((row, index) => {
            const isFocused = index === s.cursor && s.interaction.kind === 'normal';
            const cursor = isFocused ? '› ' : '  ';
            const color = isFocused ? theme.warning : theme.text;
            if (row.kind === 'create') {
              const creating = s.interaction.kind === 'createName';
              return (
                <Box key="create" flexShrink={0}>
                  <Text color={creating || isFocused ? theme.warning : theme.text} bold={isFocused}>
                    {cursor}
                    {creating ? 'name ' : '+ New template'}
                  </Text>
                  {creating ? (
                    <TextEditorDisplay
                      state={s.editEditor}
                      width={LIST_EDITOR_WIDTH}
                      placeholder="(name)"
                      focused
                      color={theme.text}
                    />
                  ) : null}
                </Box>
              );
            }
            const renaming = s.interaction.kind === 'rename' && s.interaction.name === row.name;
            const confirming =
              (s.interaction.kind === 'confirmDelete' && s.interaction.name === row.name) ||
              (s.interaction.kind === 'confirmRename' && s.interaction.oldName === row.name);
            const collides = workflowNames.has(row.name);
            return (
              <Box key={row.name} flexShrink={0}>
                <Text color={color} bold={isFocused}>
                  {cursor}
                  {`:${renaming ? '' : row.name}`}
                </Text>
                {renaming ? (
                  <TextEditorDisplay
                    state={s.editEditor}
                    width={LIST_EDITOR_WIDTH}
                    placeholder="(name)"
                    focused
                    color={theme.text}
                  />
                ) : (
                  <Text color={confirming ? theme.warning : theme.muted}>
                    {confirming ? '  ?' : collides ? '  ! workflow name' : ''}
                  </Text>
                )}
              </Box>
            );
          })}
          {list.length === 1 ? (
            <Box flexShrink={0} marginTop={1}>
              <Text color={theme.muted}>{'  '}no templates yet</Text>
            </Box>
          ) : null}
        </Box>

        <Box
          flexDirection="column"
          flexGrow={1}
          borderStyle="single"
          borderColor={theme.borderBlurred}
          paddingX={1}
        >
          {confirmingRefs !== null ? (
            <>
              <Text color={theme.warning}>
                {s.interaction.kind === 'confirmRename'
                  ? `workflow refs that will keep :${s.interaction.oldName}:`
                  : s.interaction.kind === 'confirmDelete'
                    ? `workflow refs that will break if :${s.interaction.name}: is deleted:`
                    : 'workflow refs:'}
              </Text>
              <Box flexDirection="column" flexGrow={1}>
                {confirmingRefs.length === 0 ? (
                  <Text color={theme.muted}>{'  '}(none)</Text>
                ) : (
                  confirmingRefs.map((ref) => (
                    <Text
                      key={`${ref.workflowName}/${ref.stageId}.${ref.field}`}
                      color={theme.text}
                    >
                      {`  ${formatWorkflowTemplateRef(ref)}`}
                    </Text>
                  ))
                )}
              </Box>
            </>
          ) : editingBody ? (
            <>
              <Text color={theme.muted}>
                {`body · :${s.interaction.kind === 'editBody' ? s.interaction.name : ''}:`}
              </Text>
              <TextEditorDisplay
                state={s.editEditor}
                width={Math.max(20, width - 40)}
                placeholder="(body)"
                focused
                color={theme.text}
              />
            </>
          ) : bodyForPreview !== null && selectedName !== null ? (
            <>
              <Text color={theme.muted}>{`preview · :${selectedName}:`}</Text>
              <Text color={theme.text} wrap="truncate-end">
                {previewBodyFlat(bodyForPreview)}
              </Text>
              <Box marginTop={1} flexDirection="column" flexGrow={1}>
                <Text color={theme.muted}>
                  {placeholders.length === 0
                    ? 'inputs: (none)'
                    : `inputs: ${placeholders.map((p) => `{${p}}`).join(', ')}`}
                </Text>
                <Text color={unknownRefs.length > 0 ? theme.warning : theme.muted}>
                  {unknownRefs.length === 0
                    ? 'unknown refs: (none)'
                    : `unknown refs: ${unknownRefs.map((n) => `:${n}:`).join(', ')}`}
                </Text>
                <Text color={workflowRefs.length > 0 ? theme.warning : theme.muted}>
                  {workflowRefs.length === 0
                    ? 'used by workflows: (none)'
                    : `used by (${workflowRefs.length}):`}
                </Text>
                {workflowRefs.map((ref) => (
                  <Text
                    key={`${ref.workflowName}/${ref.stageId}.${ref.field}`}
                    color={theme.warning}
                  >
                    {`  ${formatWorkflowTemplateRef(ref)}`}
                  </Text>
                ))}
                {expansion !== null ? (
                  <Text color={theme.muted} wrap="truncate-end">
                    {`expansion: ${previewBodyFlat(expansion.text, 120)}`}
                  </Text>
                ) : null}
              </Box>
            </>
          ) : (
            <Text color={theme.muted}>
              {editingName ? 'Enter a name…' : 'Select a template to preview'}
            </Text>
          )}
        </Box>
      </Box>

      {s.notice !== null ? (
        <Box marginTop={1} flexShrink={0}>
          <Text color={theme.warning}>{s.notice}</Text>
        </Box>
      ) : null}

      <Box marginTop={1} flexShrink={0}>
        <Text dimColor>
          {confirmingRefs !== null
            ? 'y: confirm · n/esc: cancel'
            : editingBody
              ? 'enter: save · shift+enter: newline · esc: cancel'
              : editingName
                ? 'enter: confirm · esc: cancel'
                : 'j/k: navigate · enter: edit · r: rename · n: new · d: delete · esc: close'}
        </Text>
      </Box>
    </Box>
  );
}

const EMPTY_STORE_STATE = {
  templates: { items: [] as readonly TemplateRecord[] },
  workflows: { items: [] as readonly WorkflowTemplate[] },
  actions: {
    templates: { remove() {}, rename() {}, save() {} },
  },
} as unknown as AppStore;

const EMPTY_STORE = {
  getState: () => EMPTY_STORE_STATE,
  getInitialState: () => EMPTY_STORE_STATE,
  setState: () => {},
  subscribe: () => () => {},
} as const;
