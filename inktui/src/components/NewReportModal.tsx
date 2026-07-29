/**
 * `NewReportModal` — name-only create flow for reports (panel “+ new report” / shared entrypoint).
 *
 * Submit → `actions.createReport(name)` → `report.create` RPC → toast + open doc.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import { applyEditorKey } from '../input/textEditor/applyEditorKey.js';
import type { EditorCommand } from '../input/textEditor/commands.js';
import { singleLineEditorPolicy } from '../input/textEditor/keyDecoder.js';
import { reduceEditor } from '../input/textEditor/operations.js';
import { plainTextProjection } from '../input/textEditor/projection.js';
import { editorAtEnd, type TextEditorState } from '../input/textEditor/state.js';
import { plainTextTopology } from '../input/textEditor/topology.js';
import type { DialogActions } from '../store/dialogs/dialogActions.js';
import { toastStore } from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import { TextEditorDisplay } from './TextEditorDisplay.js';

import '../input/dispatcher.js';

const NAME_EDITOR_WIDTH = 54;
export const NEW_REPORT_MODE_ID = 'new-report';

type NewReportIntent =
  | 'backspace'
  | 'deleteForward'
  | 'moveLeft'
  | 'moveRight'
  | 'home'
  | 'end'
  | 'deleteAll'
  | 'submit'
  | 'dismiss';

export interface NewReportModeOptions {
  readonly onSubmit?: (reportName: string) => void;
  readonly onDismiss?: () => void;
}

interface NewReportState {
  name: TextEditorState;
  error: string | null;
}

function editName(state: TextEditorState, command: EditorCommand): TextEditorState {
  return reduceEditor(state, command, {
    width: NAME_EDITOR_WIDTH,
    topology: plainTextTopology,
    projection: plainTextProjection,
  }).state;
}

export function newReportMode(
  modes: ModeStoreApi,
  actions: DialogActions,
  opts: NewReportModeOptions = {},
): Mode<NewReportIntent> {
  const id = NEW_REPORT_MODE_ID;
  const s: NewReportState = {
    name: editorAtEnd(),
    error: null,
  };

  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  const mode: Mode<NewReportIntent> = {
    id,
    presentation: 'modal',
    keymap: [
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { key: { delete: true } }, intent: 'deleteForward', description: 'delete' },
      { chord: { key: { leftArrow: true } }, intent: 'moveLeft', description: 'left' },
      { chord: { key: { rightArrow: true } }, intent: 'moveRight', description: 'right' },
      { chord: { key: { home: true } }, intent: 'home', description: 'line start' },
      { chord: { key: { end: true } }, intent: 'end', description: 'line end' },
      {
        chord: { input: 'u', key: { meta: true } },
        intent: 'deleteAll',
        description: 'clear field',
      },
      { chord: { key: { return: true } }, intent: 'submit', description: 'create report' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'backspace':
          s.name = editName(s.name, { type: 'backspace' });
          refresh();
          return;
        case 'deleteForward':
          s.name = editName(s.name, { type: 'deleteForward' });
          refresh();
          return;
        case 'moveLeft':
          s.name = editName(s.name, { type: 'moveLeft' });
          refresh();
          return;
        case 'moveRight':
          s.name = editName(s.name, { type: 'moveRight' });
          refresh();
          return;
        case 'home':
          s.name = editName(s.name, { type: 'moveLineStart' });
          refresh();
          return;
        case 'end':
          s.name = editName(s.name, { type: 'moveLineEnd' });
          refresh();
          return;
        case 'deleteAll':
          s.name = editorAtEnd();
          refresh();
          return;
        case 'submit': {
          const name = s.name.text.trim();
          if (name === '') {
            s.error = 'name required';
            refresh();
            return;
          }
          modes.getState().exit(id);
          void actions
            .createReport(name)
            .then((result) => {
              opts.onSubmit?.(result.name);
            })
            .catch((error: unknown) => {
              const message = error instanceof Error ? error.message : String(error);
              toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
            });
          return;
        }
        case 'dismiss':
          modes.getState().exit(id);
          opts.onDismiss?.();
          return;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input: string, key: Key): boolean {
      const transition = applyEditorKey(s.name, input, key, {
        policy: singleLineEditorPolicy,
        environment: {
          width: NAME_EDITOR_WIDTH,
          topology: plainTextTopology,
          projection: plainTextProjection,
        },
      });
      if (transition === null) {
        return false;
      }
      s.name = transition.state;
      s.error = null;
      refresh();
      return true;
    },
    render: () => <NewReportDialog name={s.name} error={s.error} />,
  };
  return mode;
}

function NewReportDialog({
  name,
  error,
}: {
  readonly name: TextEditorState;
  readonly error: string | null;
}): JSX.Element {
  const theme = useTheme();
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.success}
      paddingX={1}
      width={NAME_EDITOR_WIDTH + 4}
    >
      <Text bold color={theme.success}>
        New report
      </Text>
      <Text color={theme.muted}>name</Text>
      <TextEditorDisplay
        state={name}
        width={NAME_EDITOR_WIDTH}
        placeholder="report-name"
        focused
      />
      {error !== null ? <Text color={theme.error}>{error}</Text> : null}
    </Box>
  );
}
