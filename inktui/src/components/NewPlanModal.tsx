/**
 * `NewPlanModal` — the `super+p` new-plan flow: a **single-form wizard** {@link Mode} (item 3), NOT a
 * multi-step pager. One filled-out form with three focus groups, navigated top-to-bottom:
 *
 *  1. **Body textbox** (multi-line) — whatever is typed becomes the plan's markdown body. Printable
 *     chars append; Shift+Enter inserts a newline; Enter advances to the naming group.
 *  2. **Naming radio** — `auto` (mini-LLM names the plan from the body) vs `name-it-yourself`. `j/k`,
 *     `h/l`, and the arrow keys move the highlight; Enter confirms the choice and advances focus.
 *  3. **Name input** — shown only when `custom` is chosen; the typed plan name. Enter submits.
 *
 * On submit it calls `actions.createPlan(...)` → `plan.create` RPC (the service derives the name when
 * `auto` and seeds the body). A brief `creating plan…`
 * pending state covers the create request, including auto naming when selected. On success the caller's `onSubmit` runs
 * (toast + open the plan's doc pane).
 *
 * ## Field + state model (the C12 modal recipe)
 *
 * Modal state (the body/name text, the naming choice, the focused group, pending/error) lives in a
 * mutable object inside the mode factory closure — not React state (the mode is plain data). The
 * `render` thunk closes over it; after each mutation the mode store is poked (re-enter same id → new
 * stack ref → Zustand re-renders subscribers), the same pattern the spawn wizard uses.
 *
 * Bottom-bar hints come from the mode's `hints` getter (wave 1 made the BottomBar mode-aware) — there
 * is no hint line inside the modal box.
 *
 * `passThrough: false` (default): the modal captures every key while up; `onUncaptured` handles the
 * printable text chars before the dispatcher's swallow decision.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import type { Mode, ModeHint, ModeStoreApi } from '../input/modeStore.js';
import { applyEditorKey } from '../input/textEditor/applyEditorKey.js';
import type { EditorCommand } from '../input/textEditor/commands.js';
import { multilineEditorPolicy, singleLineEditorPolicy } from '../input/textEditor/keyDecoder.js';
import { reduceEditor } from '../input/textEditor/operations.js';
import { plainTextProjection } from '../input/textEditor/projection.js';
import type { TextEditorState } from '../input/textEditor/state.js';
import { editorAtEnd } from '../input/textEditor/state.js';
import { plainTextTopology } from '../input/textEditor/topology.js';
import type { CreatePlanInput, DialogActions } from '../store/dialogs/dialogActions.js';
import { toastStore } from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import { TextEditorDisplay } from './TextEditorDisplay.js';

// Import the dispatcher augmentation so Mode gets the `onUncaptured` field at the TS level.
import '../input/dispatcher.js';

/** Intent union for the new-plan form — special key actions only. Printable chars go through
 * `onUncaptured`, not the keymap, so they are not listed here. */
type NewPlanIntent =
  | 'backspace'
  | 'deleteForward'
  | 'newline'
  | 'advance'
  | 'navPrev'
  | 'navNext'
  | 'editorLeft'
  | 'editorRight'
  | 'editorUp'
  | 'editorDown'
  | 'editorHome'
  | 'editorEnd'
  | 'submit'
  | 'dismiss';

/** The naming choice the radio group offers. */
type Naming = 'auto' | 'custom';

/** Which focus group has the highlight. `body` → naming → (`name` only when custom is chosen). */
type FocusGroup = 'body' | 'naming' | 'name';

/** Options passed to the mode factory. */
export interface NewPlanModeOptions {
  /** Called with the FINAL plan name after a successful submit (fired after mode exits). The shell
   * uses it to open the plan's doc pane. */
  readonly onSubmit?: (planName: string) => void;
  /** Called when the form is dismissed without submitting (fired after mode exits). */
  readonly onDismiss?: () => void;
}

/** The stable mode id so a re-enter is idempotent. */
export const NEW_PLAN_MODE_ID = 'new-plan';

/**
 * Mutable local state inside the mode closure. Not React state — the mode is plain data.
 * Mutated in `onIntent` / `onUncaptured`; `render` reads it at call time.
 */
interface NewPlanState {
  body: TextEditorState;
  naming: Naming;
  planName: TextEditorState;
  focus: FocusGroup;
  /** True while the `plan.create` RPC is in flight. */
  pending: boolean;
  error: string | null;
}

/** The two naming options, in highlight order (left→right / top→bottom). */
const NAMING_ORDER: readonly Naming[] = ['auto', 'custom'];

/**
 * Build the new-plan {@link Mode}. Pass `modes` (for self-dismiss), `actions` (for the RPC), and
 * optional callbacks. Enter via: `modes.getState().enter(newPlanMode(modes, actions, opts))`.
 */
export function newPlanMode(
  modes: ModeStoreApi,
  actions: DialogActions,
  opts: NewPlanModeOptions = {},
): Mode<NewPlanIntent> {
  const id = NEW_PLAN_MODE_ID;

  const s: NewPlanState = {
    body: editorAtEnd(),
    naming: 'auto',
    planName: editorAtEnd(),
    focus: 'body',
    pending: false,
    error: null,
  };
  // Updated from the render-time modal allocation. The editor itself retains no geometry; the mode
  // supplies the latest observed content width with each key transition.
  let editorWidth = 80;

  // Re-render by poking the mode store: re-enter the same id (idempotent focus, new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  /** Fire the `plan.create` RPC and dismiss after the durable create completes. */
  function submit(): void {
    if (s.pending) {
      return;
    }
    const autoName = s.naming === 'auto';
    const planName = s.planName.text.trim();
    if (!autoName && planName.length === 0) {
      s.error = 'Plan name is required (or pick "auto").';
      s.focus = 'name';
      refresh();
      return;
    }
    s.pending = true;
    s.error = null;
    refresh();
    const body = s.body.text;
    const message = body.trim().length > 0 ? body : undefined;
    const input: CreatePlanInput = autoName
      ? { body, autoName: true, ...(message !== undefined ? { message } : {}) }
      : { body, autoName: false, planName, ...(message !== undefined ? { message } : {}) };
    void actions
      .createPlan(input)
      .then((result) => {
        modes.getState().exit(id);
        opts.onSubmit?.(result.plan_name);
      })
      .catch((error: unknown) => {
        // The modal is still up (we only exit on success), so surface the error inline AND keep the
        // form so the user can retry. The toast covers the case where focus has already moved.
        const text = error instanceof Error ? error.message : String(error);
        s.pending = false;
        s.error = text;
        refresh();
        toastStore.getState().push(text, { severity: 'error', ttlMs: 12000 });
      });
  }

  /** Move the naming-radio highlight by `delta` (wrapping), used by both axes (h/l, j/k, arrows). */
  function moveNaming(delta: number): void {
    const i = NAMING_ORDER.indexOf(s.naming);
    const next = (i + delta + NAMING_ORDER.length) % NAMING_ORDER.length;
    s.naming = NAMING_ORDER[next] ?? 'auto';
    refresh();
  }

  function applyToFocused(command: EditorCommand): void {
    if (s.focus === 'naming') return;
    const key = s.focus === 'body' ? 'body' : 'planName';
    const state = s[key];
    s[key] = reduceEditor(state, command, {
      width: editorWidth,
      topology: plainTextTopology,
      projection: plainTextProjection,
    }).state;
    s.error = null;
    refresh();
  }

  const mode: Mode<NewPlanIntent> = {
    id,
    presentation: 'modal',
    // Hints live in the bottom bar (wave 1 mode-aware BottomBar). A getter so the bar always shows the
    // CURRENT focus group's keys (refresh() re-enters the frame, re-deriving them).
    get hints(): readonly ModeHint[] {
      return newPlanHints(s.focus);
    },
    // Structural keys only — printable chars (body/name text + the radio's h/l/j/k) ride `onUncaptured`.
    keymap: [
      { chord: { key: { shift: true, return: true } }, intent: 'newline', description: 'newline' },
      { chord: { key: { return: true } }, intent: 'advance', description: 'confirm' },
      { chord: { key: { upArrow: true } }, intent: 'editorUp', description: 'up' },
      { chord: { key: { downArrow: true } }, intent: 'editorDown', description: 'down' },
      { chord: { key: { leftArrow: true } }, intent: 'editorLeft', description: 'left' },
      { chord: { key: { rightArrow: true } }, intent: 'editorRight', description: 'right' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { key: { delete: true } }, intent: 'deleteForward', description: 'delete' },
      { chord: { key: { home: true } }, intent: 'editorHome', description: 'line start' },
      { chord: { key: { end: true } }, intent: 'editorEnd', description: 'line end' },
      { chord: { key: { tab: true } }, intent: 'submit', description: 'create' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      if (s.pending && intent !== 'dismiss') {
        return; // ignore edits while the RPC is in flight
      }
      switch (intent) {
        case 'backspace': {
          applyToFocused({ type: 'backspace' });
          return;
        }
        case 'deleteForward':
          applyToFocused({ type: 'deleteForward' });
          return;
        case 'newline': {
          // Shift+Enter: a literal newline in the body box (a no-op in the radio/name groups).
          if (s.focus === 'body') {
            applyToFocused({ type: 'insertNewline' });
          }
          return;
        }
        case 'advance': {
          // Enter: confirm the current group and advance focus (or submit at the end).
          if (s.focus === 'body') {
            s.focus = 'naming';
            refresh();
          } else if (s.focus === 'naming') {
            if (s.naming === 'custom') {
              s.focus = 'name';
              refresh();
            } else {
              submit();
            }
          } else {
            submit();
          }
          return;
        }
        case 'navPrev': {
          if (s.focus === 'naming') {
            moveNaming(-1);
          } else {
            applyToFocused({ type: 'moveLeft' });
          }
          return;
        }
        case 'navNext': {
          if (s.focus === 'naming') {
            moveNaming(1);
          } else {
            applyToFocused({ type: 'moveRight' });
          }
          return;
        }
        case 'editorLeft':
          if (s.focus === 'naming') moveNaming(-1);
          else applyToFocused({ type: 'moveLeft' });
          return;
        case 'editorRight':
          if (s.focus === 'naming') moveNaming(1);
          else applyToFocused({ type: 'moveRight' });
          return;
        case 'editorUp':
          if (s.focus === 'naming') moveNaming(-1);
          else applyToFocused({ type: 'moveVisualUp' });
          return;
        case 'editorDown':
          if (s.focus === 'naming') moveNaming(1);
          else applyToFocused({ type: 'moveVisualDown' });
          return;
        case 'editorHome':
          applyToFocused({ type: 'moveLineStart' });
          return;
        case 'editorEnd':
          applyToFocused({ type: 'moveLineEnd' });
          return;
        case 'submit': {
          // Tab: submit from anywhere (a quick-create escape hatch).
          submit();
          return;
        }
        case 'dismiss': {
          modes.getState().exit(id);
          opts.onDismiss?.();
          return;
        }
        default:
          return intent satisfies never;
      }
    },
    // onUncaptured: printable text entry + the radio's hjkl navigation. The dispatcher calls this when
    // the declared keymap has no match (the C12 hook). Tab/Esc stay with the mode via the decoder.
    onUncaptured(input: string, key: Key): boolean {
      if (s.pending) {
        return true; // swallow edits while submitting
      }
      if (s.focus === 'naming') {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.tab) {
          return false;
        }
        // hjkl moves the radio highlight (arrows ride the keymap above).
        if (input === 'h' || input === 'k') {
          moveNaming(-1);
          return true;
        }
        if (input === 'l' || input === 'j') {
          moveNaming(1);
          return true;
        }
        return true; // swallow other chars while the radio is focused (no text field here)
      }
      const field = s.focus === 'body' ? 'body' : 'planName';
      const transition = applyEditorKey(s[field], input, key, {
        policy: s.focus === 'body' ? multilineEditorPolicy : singleLineEditorPolicy,
        environment: {
          width: editorWidth,
          topology: plainTextTopology,
          projection: plainTextProjection,
        },
      });
      if (transition === null) return false;
      s[field] = transition.state;
      s.error = null;
      refresh();
      return true;
    },
    render: () => (
      <NewPlanForm
        body={s.body}
        naming={s.naming}
        planName={s.planName}
        focus={s.focus}
        pending={s.pending}
        error={s.error}
        onContentWidth={(width) => {
          editorWidth = width;
        }}
      />
    ),
  };

  return mode;
}

/** The bottom-bar hints for the active focus group. Pure over the group so it tests without the bar. */
export function newPlanHints(focus: FocusGroup): readonly ModeHint[] {
  const cancel: ModeHint = { key: 'esc', description: 'cancel' };
  switch (focus) {
    case 'body':
      return [
        { key: 'shift+enter', description: 'newline' },
        { key: 'enter', description: 'next' },
        cancel,
      ];
    case 'naming':
      return [
        { key: 'h/l/j/k/←→', description: 'choose' },
        { key: 'enter', description: 'confirm' },
        cancel,
      ];
    case 'name':
      return [{ key: 'enter', description: 'create' }, cancel];
    default:
      return [cancel];
  }
}

/** The form's visual presentation — a pure function of its props (rule 1). No store/bus knowledge. */
function NewPlanForm({
  body,
  naming,
  planName,
  focus,
  pending,
  error,
  onContentWidth,
}: {
  readonly body: TextEditorState;
  readonly naming: Naming;
  readonly planName: TextEditorState;
  readonly focus: FocusGroup;
  readonly pending: boolean;
  readonly error: string | null;
  readonly onContentWidth: (width: number) => void;
}): JSX.Element {
  const theme = useTheme();
  // The form fills ~90% of the available screen real estate so a long plan body has room to wrap and
  // read (item 3): width is 90% of the live terminal columns (floored so it stays usable on a narrow
  // pane), and `height="90%"` fills 90% of the Overlay's body slot — the modal floats centered with a
  // ~5% margin all round. The body textbox `flexGrow={1}`s to claim the tall middle, pushing the
  // naming/name controls to the bottom.
  const { columns } = useTerminalSize();
  const width = Math.max(48, Math.floor(columns * 0.9));
  const contentWidth = Math.max(1, width - 6);
  onContentWidth(contentWidth);
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={width}
      height="90%"
    >
      <Text bold color={theme.heading}>
        New Plan
      </Text>

      {/* Body textbox (multi-line) — grows to fill the tall modal so the draft has room to wrap. */}
      <Box marginTop={1} flexDirection="column" flexGrow={1}>
        <Box flexDirection="column" flexShrink={0}>
          <Text color={focus === 'body' ? theme.text : theme.muted}>Plan body:</Text>
          <TextEditorDisplay
            state={body}
            width={contentWidth}
            placeholder="Describe the plan…"
            focused={focus === 'body'}
            color={focus === 'body' ? theme.text : theme.muted}
          />
        </Box>
      </Box>

      {/* Naming radio group. */}
      <Box marginTop={1} flexDirection="column">
        <Text color={focus === 'naming' ? theme.text : theme.muted}>Name:</Text>
        <Box flexDirection="row" columnGap={3}>
          <NamingOption label="auto" selected={naming === 'auto'} active={focus === 'naming'} />
          <NamingOption
            label="name it myself"
            selected={naming === 'custom'}
            active={focus === 'naming'}
          />
        </Box>
      </Box>

      {/* Custom-name input — shown only when the custom radio is chosen. */}
      {naming === 'custom' && (
        <Box marginTop={1} flexDirection="column">
          <Box flexDirection="column" flexShrink={0}>
            <Text color={focus === 'name' ? theme.text : theme.muted}>Plan name:</Text>
            <TextEditorDisplay
              state={planName}
              width={contentWidth}
              placeholder="e.g. refactor-auth"
              focused={focus === 'name'}
              color={focus === 'name' ? theme.text : theme.muted}
            />
          </Box>
        </Box>
      )}

      {pending && (
        <Box marginTop={1}>
          <Text color={theme.muted}>creating plan…</Text>
        </Box>
      )}
      {error !== null && (
        <Box marginTop={1}>
          <Text color={theme.error}>{error}</Text>
        </Box>
      )}
    </Box>
  );
}

/** One radio option: a `( )`/`(•)` marker + label, highlighted when the group is focused + selected. */
function NamingOption({
  label,
  selected,
  active,
}: {
  readonly label: string;
  readonly selected: boolean;
  readonly active: boolean;
}): JSX.Element {
  const theme = useTheme();
  const color = selected && active ? theme.heading : selected ? theme.text : theme.muted;
  return (
    <Text color={color} bold={selected && active}>
      {selected ? '(•) ' : '( ) '}
      {label}
    </Text>
  );
}
