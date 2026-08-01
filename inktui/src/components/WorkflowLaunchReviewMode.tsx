/**
 * Shared saved-workflow launch review.  A review is intentionally mandatory, including for a
 * template with no inputs: compilation is authoritative and `workflow.start` is reachable only
 * from the explicit Launch confirmation below.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeHint, ModeStoreApi } from '../input/modeStore.js';
import '@murder/ui-core/input/dispatcher.js';
import type { AppStoreApi } from '@murder/ui-core/store/store.js';
import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';
import {
  requiredInputIssues,
  type WizardField,
  wizardFieldsFromCompileResult,
} from '@murder/ui-core/workflowEditor/compile.js';
import { Pane } from './Pane.js';

export const WORKFLOW_LAUNCH_REVIEW_MODE_ID = 'workflow-launch-review';

type Intent = 'launch' | 'next' | 'previous' | 'backspace' | 'newline' | 'dismiss';

interface ReviewState {
  readonly workflow: WorkflowTemplate;
  status: 'loading' | 'ready' | 'error';
  fields: readonly WizardField[];
  values: Record<string, string>;
  cursor: number;
  error: string | null;
}

export interface WorkflowLaunchReviewModeOptions {
  readonly workflow: WorkflowTemplate;
  /** A just-saved canonical record from the editor may be compiled inline before its registry
   * projection arrives. Library launches omit this and compile by exact saved name. */
  readonly compileTemplate?: WorkflowTemplate;
  /** Unsaved prompt-template edits are relevant to editor preview; persisted library launches omit it. */
  readonly promptTemplates?: Readonly<Record<string, string>>;
  readonly onLaunched?: (workflow: WorkflowTemplate) => void;
}

export function workflowLaunchReviewMode(
  modes: ModeStoreApi,
  app: AppStoreApi,
  options: WorkflowLaunchReviewModeOptions,
): Mode<Intent> {
  const id = WORKFLOW_LAUNCH_REVIEW_MODE_ID;
  const s: ReviewState = {
    workflow: options.workflow,
    status: 'loading',
    fields: [],
    values: {},
    cursor: 0,
    error: null,
  };

  function refresh(): void {
    const frame = modes.getState().stack.find((item) => item.mode.id === id);
    if (frame !== undefined) modes.getState().enter(frame.mode);
  }

  void app
    .getState()
    .actions.workflows.compile({
      ...(options.compileTemplate === undefined ? { name: s.workflow.name } : { template: options.compileTemplate }),
      ...(options.promptTemplates === undefined ? {} : { promptTemplates: options.promptTemplates }),
    })
    .then((result) => {
      const compiled = wizardFieldsFromCompileResult(result);
      const blocking = compiled.issues.find((issue) => issue.severity === 'error');
      if (!result.ok || blocking !== undefined) {
        s.status = 'error';
        s.error = blocking?.message ?? 'Workflow template compile failed.';
      } else {
        s.status = 'ready';
        s.fields = compiled.fields;
        s.values = Object.fromEntries(
          compiled.fields.map((field) => [field.name, field.defaultValue]),
        );
      }
      refresh();
    })
    .catch((error: unknown) => {
      s.status = 'error';
      s.error = error instanceof Error ? error.message : String(error);
      refresh();
    });

  function launch(): void {
    if (s.status !== 'ready') return;
    const missing = requiredInputIssues(s.fields, s.values);
    if (missing.length > 0) {
      s.error = missing[0]?.message ?? 'Required workflow input is not filled.';
      refresh();
      return;
    }
    modes.getState().exit(id);
    void app.getState().actions.workflows.run(s.workflow.name, s.values);
    options.onLaunched?.(s.workflow);
  }

  return {
    id,
    presentation: 'fullscreen',
    get hints(): readonly ModeHint[] {
      if (s.status === 'loading') return [{ key: 'esc', description: 'cancel' }];
      if (s.status === 'error') return [{ key: 'esc', description: 'back' }];
      return s.fields[s.cursor]?.kind === 'multiline'
        ? [
            { key: 'type', description: 'input value' },
            { key: 'shift+enter', description: 'newline' },
            { key: 'tab', description: 'next field' },
            { key: 'enter', description: 'launch' },
            { key: 'esc', description: 'back' },
          ]
        : [
            {
              key: s.fields.length === 0 ? 'enter' : 'type',
              description: s.fields.length === 0 ? 'launch' : 'input value',
            },
            ...(s.fields.length === 0 ? [] : [{ key: 'tab', description: 'next field' }]),
            { key: 'enter', description: 'launch' },
            { key: 'esc', description: 'back' },
          ];
    },
    keymap: [
      { chord: { key: { return: true, shift: true } }, intent: 'newline', description: 'newline' },
      { chord: { key: { return: true } }, intent: 'launch', description: 'launch' },
      { chord: { key: { tab: true } }, intent: 'next', description: 'next field' },
      {
        chord: { key: { tab: true, shift: true } },
        intent: 'previous',
        description: 'previous field',
      },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'erase' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'back' },
    ],
    onIntent(intent): void {
      if (intent === 'dismiss') {
        modes.getState().exit(id);
        return;
      }
      if (s.status !== 'ready') return;
      if (intent === 'launch') {
        launch();
        return;
      }
      const field = s.fields[s.cursor];
      if (intent === 'next' && s.fields.length > 0) {
        s.cursor = (s.cursor + 1) % s.fields.length;
      } else if (intent === 'previous' && s.fields.length > 0) {
        s.cursor = (s.cursor - 1 + s.fields.length) % s.fields.length;
      } else if (intent === 'backspace' && field !== undefined) {
        s.values[field.name] = (s.values[field.name] ?? '').slice(0, -1);
        s.error = null;
      } else if (intent === 'newline' && field?.kind === 'multiline') {
        s.values[field.name] = `${s.values[field.name] ?? ''}\n`;
        s.error = null;
      }
      refresh();
    },
    onUncaptured(input: string, key: Key): boolean {
      if (s.status !== 'ready' || input.length === 0 || key.ctrl || key.meta) return false;
      const field = s.fields[s.cursor];
      if (field === undefined) return false;
      s.values[field.name] = `${s.values[field.name] ?? ''}${input}`;
      s.error = null;
      refresh();
      return true;
    },
    render: () => <WorkflowLaunchReviewSurface state={s} />,
  };
}

function WorkflowLaunchReviewSurface({ state }: { readonly state: ReviewState }): JSX.Element {
  const theme = useTheme();
  const inputs = Object.keys(state.workflow.inputs ?? {});
  return (
    <Box width="100%" height="100%" flexDirection="column" overflow="hidden">
      <Pane
        title="Workflow launch review"
        titleExtra={<Text color={theme.muted}>{`  ${state.workflow.name}`}</Text>}
        focused
        footerLeft={
          <Text color={state.error === null ? theme.muted : theme.error} wrap="truncate-end">
            {state.error ??
              (state.status === 'loading'
                ? 'Compiling template…'
                : 'Review inputs, then press Enter to launch.')}
          </Text>
        }
      >
        <Box flexDirection="column" paddingX={1}>
          <Text bold color={theme.heading} wrap="truncate-end">
            {state.workflow.name}
          </Text>
          <Text color={theme.muted} wrap="truncate-end">
            {state.workflow.description?.trim() || 'No description'}
          </Text>
          <Box height={1} />
          <Text
            color={theme.muted}
          >{`Stages: ${state.workflow.stages?.length ?? 0} · Declared inputs: ${inputs.length} · Mode: ${state.workflow.mode ?? 'static'}`}</Text>
          <Box height={1} />
          {state.status === 'loading' ? (
            <Text color={theme.muted}>Compiling declared and inferred inputs…</Text>
          ) : null}
          {state.status === 'error' ? (
            <Text color={theme.error}>{state.error ?? 'Compile failed.'}</Text>
          ) : null}
          {state.status === 'ready' && state.fields.length === 0 ? (
            <Text color={theme.text}>
              This workflow has no launch inputs. Press Enter to launch.
            </Text>
          ) : null}
          {state.status === 'ready'
            ? state.fields.map((field, index) => {
                const active = index === state.cursor;
                const value = state.values[field.name] ?? '';
                return (
                  <Box key={field.name} flexDirection="column" marginBottom={1}>
                    <Text color={active ? theme.accent : theme.muted} bold={active}>
                      {`${active ? '▌' : ' '} ${field.label}${field.required ? ' *' : ''}${field.kind === 'multiline' ? ' (multiline)' : ''}`}
                    </Text>
                    <Text color={value === '' && field.required ? theme.warning : theme.text}>
                      {active
                        ? `${value.replaceAll('\n', '↵')}█`
                        : value || (field.required ? 'required' : 'optional')}
                    </Text>
                  </Box>
                );
              })
            : null}
        </Box>
      </Pane>
    </Box>
  );
}
