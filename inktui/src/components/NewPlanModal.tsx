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
 * `auto` and seeds the body). A brief `naming…`
 * pending state covers the auto path's mini-LLM round-trip. On success the caller's `onSubmit` runs
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
import type { CreatePlanInput, DialogActions } from '../store/dialogs/dialogActions.js';
import { toastStore } from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import { deleteLastChar, insertChar, MultiLineText, TextInput } from './TextInput.js';

// Import the dispatcher augmentation so Mode gets the `onUncaptured` field at the TS level.
import '../input/dispatcher.js';

/** Intent union for the new-plan form — special key actions only. Printable chars go through
 * `onUncaptured`, not the keymap, so they are not listed here. */
type NewPlanIntent =
  | 'backspace'
  | 'newline'
  | 'advance'
  | 'navPrev'
  | 'navNext'
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
  body: string;
  naming: Naming;
  planName: string;
  focus: FocusGroup;
  /** True while the `plan.create` RPC is in flight (the `naming…` pending state). */
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
    body: '',
    naming: 'auto',
    planName: '',
    focus: 'body',
    pending: false,
    error: null,
  };

  // Re-render by poking the mode store: re-enter the same id (idempotent focus, new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  /** Fire the `plan.create` RPC and dismiss. Exit-then-act so focus restores before the async call;
   * a `naming…` pending state is shown briefly for the auto path's mini-LLM round-trip. */
  function submit(): void {
    if (s.pending) {
      return;
    }
    const autoName = s.naming === 'auto';
    const planName = s.planName.trim();
    if (!autoName && planName.length === 0) {
      s.error = 'Plan name is required (or pick "auto").';
      s.focus = 'name';
      refresh();
      return;
    }
    s.pending = true;
    s.error = null;
    refresh();
    const body = s.body;
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
      { chord: { key: { upArrow: true } }, intent: 'navPrev', description: 'prev' },
      { chord: { key: { downArrow: true } }, intent: 'navNext', description: 'next' },
      { chord: { key: { leftArrow: true } }, intent: 'navPrev', description: 'prev' },
      { chord: { key: { rightArrow: true } }, intent: 'navNext', description: 'next' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { key: { tab: true } }, intent: 'submit', description: 'create' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      if (s.pending && intent !== 'dismiss') {
        return; // ignore edits while the RPC is in flight
      }
      switch (intent) {
        case 'backspace': {
          if (s.focus === 'body') {
            s.body = deleteLastChar(s.body);
          } else if (s.focus === 'name') {
            s.planName = deleteLastChar(s.planName);
          }
          refresh();
          return;
        }
        case 'newline': {
          // Shift+Enter: a literal newline in the body box (a no-op in the radio/name groups).
          if (s.focus === 'body') {
            s.body = `${s.body}\n`;
            refresh();
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
          }
          return;
        }
        case 'navNext': {
          if (s.focus === 'naming') {
            moveNaming(1);
          }
          return;
        }
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
    // the declared keymap has no match (the C12 hook).
    onUncaptured(input: string, key: Key): boolean {
      if (input.length === 0 || key.ctrl || key.meta || key.escape) {
        return false; // special/modified key — not our char to handle
      }
      if (s.pending) {
        return true; // swallow edits while submitting
      }
      if (s.focus === 'naming') {
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
      if (s.focus === 'body') {
        s.body = insertChar(s.body, input);
      } else {
        s.planName = insertChar(s.planName, input);
      }
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
}: {
  readonly body: string;
  readonly naming: Naming;
  readonly planName: string;
  readonly focus: FocusGroup;
  readonly pending: boolean;
  readonly error: string | null;
}): JSX.Element {
  const theme = useTheme();
  // The form fills ~90% of the available screen real estate so a long plan body has room to wrap and
  // read (item 3): width is 90% of the live terminal columns (floored so it stays usable on a narrow
  // pane), and `height="90%"` fills 90% of the Overlay's body slot — the modal floats centered with a
  // ~5% margin all round. The body textbox `flexGrow={1}`s to claim the tall middle, pushing the
  // naming/name controls to the bottom.
  const { columns } = useTerminalSize();
  const width = Math.max(48, Math.floor(columns * 0.9));
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
        <Text color={focus === 'body' ? theme.text : theme.muted}>Plan body:</Text>
        <MultiLineText
          value={body}
          placeholder="Describe the plan…"
          focused={focus === 'body'}
          color={focus === 'body' ? theme.text : theme.muted}
        />
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
          <Text color={focus === 'name' ? theme.text : theme.muted}>Plan name:</Text>
          <TextInput
            value={planName}
            placeholder="e.g. refactor-auth"
            focused={focus === 'name'}
            color={focus === 'name' ? theme.text : theme.muted}
          />
        </Box>
      )}

      {pending && (
        <Box marginTop={1}>
          <Text color={theme.muted}>naming…</Text>
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
