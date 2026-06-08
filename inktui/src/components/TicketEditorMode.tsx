/**
 * TicketEditorMode — the ticket body editor as an **in-layout C7M mode**.
 *
 * ## Vim-emulator package spike (C8 deliverable)
 *
 * Evaluated packages (2026-06-08):
 *  - `ink-text-input@6.0.0` — single-line only; no multi-line/vim capability.
 *  - `ink-editor@2.1.1` — markdown WYSIWYG over CodeMirror; ~2.8MB, DOM-assuming deps
 *    (CodeMirror's `@codemirror/view` renders to a DOM canvas), incompatible with Ink's
 *    yoga-layout terminal renderer. Not usable.
 *  - `@inkjs/ui@2.0.0` — UI component collection; no editor or vim mode.
 *  - npm search for `"textarea" "multiline" "vim"` returned no Ink-compatible results with
 *    active maintenance and Ink 7 / React 19 compatibility.
 *
 * **Decision: custom minimal modal-edit component** — sanctioned by the plan ("if no good vim
 * package exists … decide a fallback (a minimal modal-edit component)"). The requirement is
 * narrow: edit a body string, flip `[ ]`/`[x]` lines, a duration field. "Vim-in-place editing"
 * means panels stay visible (inlayout mode), not a full vim clone. Implemented here with
 * normal/insert modes, line navigation, character insert/delete, and checklist toggle.
 *
 * ## Design (the in-layout C7M mode recipe — ONE input owner, rule 5)
 *
 * 1. **Declare a Mode** — `ticketEditorMode(modes, store, options)` builds a {@link Mode} with
 *    `presentation: 'inlayout'`, a declared keymap for the special keys (esc/ctrl+s/tab/return/
 *    backspace/arrows), an `onUncaptured` for printable chars + vim command letters, and a `render`
 *    thunk over a pure component.
 * 2. **Enter it** — `TicketsPanel`'s `'open'` intent calls `modes.enter(ticketEditorMode(...))`.
 *    This saves the current focus; the dispatcher's layer 0 captures all keys for the mode.
 * 3. **Text input via the ONE root dispatcher** — there is NO second `useInput` here. Every key the
 *    editor handles arrives through the single root dispatcher (rule 5): a matching declared chord
 *    fires the mode's `onIntent`; any other key is offered to the mode's `onUncaptured` (the C12
 *    dispatcher extension) before the dispatcher's swallow decision. The editor's vim mode, cursor
 *    line, pending-`d`, and schedule-field focus are mutable state in this factory's closure (NOT
 *    React state) — exactly the {@link ./NewPlanModal.js} modal pattern — mutated by the handlers,
 *    then `refresh()` re-enters the mode id to re-render. The editable body itself lives in the
 *    `ticketDetail` store slice and is mutated only through its actions (rule 3).
 * 4. **Dismiss restores focus** — `onIntent` calls `modes.exit(id)`, the primitive's job.
 *
 * ## Vim modes
 *
 * NORMAL mode: cursor-line navigation (`j`/`k` or arrows), `i` → INSERT, `dd` → delete line,
 *   `x` → toggle checklist item on cursor line, `q` → discard (Esc also dismisses),
 *   `ctrl+s` → save (also in INSERT), `tab` → focus the schedule field.
 * INSERT mode: printable chars appended to cursor line; `Backspace` → delete last char;
 *   `Return` → new line after cursor; `Esc` → dismiss (the mode's Esc chord). The editor does not
 *   distinguish "Esc back to NORMAL" from "Esc discard" — Esc is the mode's declared dismiss chord.
 *
 * ## What this is the reference for (C12 plan/note editors)
 *
 * C12 builds the plan and note editors by copying this file. The in-layout mode pattern (declare
 * Mode → enter() from panel → keymap+onUncaptured capture all input → save/dismiss → focus
 * restored) is the copyable artifact. The mode's keymap + onIntent + onUncaptured is identical in
 * shape to {@link ./NewPlanModal.js}; the editor adds vim mode/cursor closure state.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import { type JSX, useCallback } from 'react';
import { useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import { useInputStores } from '../hooks/useInputStores.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import type { AppStoreApi } from '../store/store.js';
import type { TicketFrontmatter } from '../store/ticketDetail/ticketDetailSlice.js';

// Import the dispatcher augmentation so Mode gets the `onUncaptured` field at the TS level.
// The augmentation is declared in dispatcher.ts; importing it brings the declaration into scope.
import '../input/dispatcher.js';

// ── Mode declaration ────────────────────────────────────────────────────────────────────────────

/**
 * The editor mode's intent union — the special keys the declared keymap routes. Printable
 * characters and the vim command letters (`j`/`k`/`i`/`x`/`d`/`q`) are NOT here: they flow through
 * `onUncaptured`, because what they do is context-dependent on the vim mode (a `j` is navigation in
 * NORMAL but a literal character in INSERT), which a static keymap cannot express.
 */
type EditorIntent =
  | 'save'
  | 'dismiss'
  | 'navUp'
  | 'navDown'
  | 'newline'
  | 'backspace'
  | 'toggleSchedule';

/** Stable mode id so re-entry is idempotent (the modeStore pattern). */
const EDITOR_MODE_ID = 'ticket-editor';

/** What the caller supplies to open the editor. */
export interface TicketEditorModeOptions {
  /** Called when the user saves (ctrl+s). The action layer (via the store) does the bus call. */
  readonly onSave: () => void;
  /** Called when the user dismisses without saving (Esc, `q`). */
  readonly onDiscard: () => void;
}

/** Vim editing modes. NORMAL: navigate + commands; INSERT: type text. */
type VimMode = 'normal' | 'insert';

/**
 * Mutable local state inside the mode closure. Not React state — the mode is plain data (the
 * {@link ./NewPlanModal.js} pattern). Mutated in `onIntent`/`onUncaptured`; `render` reads it at
 * call time. The editable body is NOT here — it lives in the `ticketDetail` store slice and is read
 * via the store handle / mutated via its actions (rule 3).
 */
interface EditorUiState {
  vimMode: VimMode;
  cursorLine: number;
  /** Pending `d` for `dd` (delete-line) detection. */
  pendingD: boolean;
  /** When true, keystrokes edit the schedule duration field instead of the body. */
  scheduleFocused: boolean;
}

/** Split a body string into lines. Always returns at least one element. */
function toLines(body: string): string[] {
  const lines = body.split('\n');
  return lines.length === 0 ? [''] : lines;
}

/** Join lines back to a body string. */
function fromLines(lines: readonly string[]): string {
  return lines.join('\n');
}

/** Toggle a checklist line: `- [ ] text` ↔ `- [x] text`. Non-checklist lines are returned unchanged. */
function toggleChecklist(line: string): string {
  const unchecked = line.replace(/^(\s*-\s*)\[ \]/, '$1[x]');
  if (unchecked !== line) {
    return unchecked;
  }
  const checked = line.replace(/^(\s*-\s*)\[x\]/, '$1[ ]');
  return checked !== line ? checked : line;
}

/** Is a line a checklist item (either checked or unchecked)? */
function isChecklistLine(line: string): boolean {
  return /^\s*-\s*\[[x ]\]/.test(line);
}

/**
 * Build the ticket editor {@link Mode}. `presentation: 'inlayout'` keeps surrounding panels
 * visible (no `$EDITOR`-blank). The mode is the SINGLE input owner (rule 5): its keymap handles the
 * special keys and its `onUncaptured` handles printable chars + vim commands — there is no second
 * `useInput`. Pass `modes` (for self-dismiss) and the `store` handle (to read the body slice and
 * dispatch its actions inside the handlers; rule 3 — the mode dispatches actions, never the bus).
 *
 * C12 copies this function to build plan/note editors — swap `render`, the mode `id`, and the
 * body-bearing slice.
 */
export function ticketEditorMode(
  modes: ModeStoreApi,
  store: AppStoreApi,
  options: TicketEditorModeOptions,
): Mode<EditorIntent> {
  const id = EDITOR_MODE_ID;

  // Mutable UI state in the closure — not React state (the mode is plain data, like NewPlanModal).
  const ui: EditorUiState = {
    vimMode: 'normal',
    cursorLine: 0,
    pendingD: false,
    scheduleFocused: false,
  };

  // Re-render by poking the mode store: re-enter the same id (idempotent focus, new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  // Slice/action accessors — read the body from the slice, write via actions (rule 3).
  const detail = () => store.getState().ticketDetail;
  const acts = () => store.getState().actions.ticketDetail;

  /** Current body lines (empty when nothing loaded), and the cursor clamped into range. */
  function bodyLines(): string[] {
    const body = detail().editedBody;
    return body !== null ? toLines(body) : [];
  }
  function clampedCursor(lines: readonly string[]): number {
    return lines.length === 0 ? 0 : Math.min(ui.cursorLine, lines.length - 1);
  }
  function writeBody(lines: readonly string[]): void {
    acts().setEditedBody(fromLines(lines));
  }

  // ── NORMAL-mode command letters (j/k/i/x/dd/q). Returns true if the char was a command. ──
  function handleNormalCommand(input: string): boolean {
    const lines = bodyLines();
    const cursor = clampedCursor(lines);
    switch (input) {
      case 'j':
        ui.cursorLine = Math.min(cursor + 1, Math.max(lines.length - 1, 0));
        ui.pendingD = false;
        return true;
      case 'k':
        ui.cursorLine = Math.max(cursor - 1, 0);
        ui.pendingD = false;
        return true;
      case 'i':
        ui.vimMode = 'insert';
        ui.pendingD = false;
        return true;
      case 'x': {
        const line = lines[cursor];
        if (line !== undefined && isChecklistLine(line)) {
          const next = [...lines];
          next[cursor] = toggleChecklist(line);
          writeBody(next);
        }
        ui.pendingD = false;
        return true;
      }
      case 'd': {
        if (ui.pendingD) {
          // Second 'd': delete the cursor line.
          if (lines.length > 0) {
            const next = [...lines];
            next.splice(cursor, 1);
            if (next.length === 0) {
              next.push('');
            }
            writeBody(next);
            ui.cursorLine = Math.min(cursor, next.length - 1);
          }
          ui.pendingD = false;
        } else {
          ui.pendingD = true;
        }
        return true;
      }
      case 'q':
        // Discard: exit the mode then run the caller's discard handler (exit-then-act).
        modes.getState().exit(id);
        options.onDiscard();
        ui.pendingD = false;
        return true;
      default:
        // Any other key clears a pending 'd' but is not a command (let it be swallowed).
        ui.pendingD = false;
        return false;
    }
  }

  /** INSERT-mode printable character: append to the cursor line. Returns true (always consumed). */
  function handleInsertChar(input: string): boolean {
    const lines = bodyLines();
    const cursor = clampedCursor(lines);
    const next = lines.length === 0 ? [''] : [...lines];
    const line = next[cursor] ?? '';
    next[cursor] = line + input;
    writeBody(next);
    return true;
  }

  const mode: Mode<EditorIntent> = {
    id,
    presentation: 'inlayout',
    // No passThrough: the editor captures everything. Panel/global chords do NOT fire underneath.
    keymap: [
      // Dismiss / save — handled identically to confirmMode.
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'discard & close' },
      { chord: { input: 's', key: { ctrl: true } }, intent: 'save', description: 'save & close' },
      // Tab toggles the schedule field focus.
      { chord: { key: { tab: true } }, intent: 'toggleSchedule', description: 'schedule field' },
      // Arrow navigation (NORMAL line nav; mirrors j/k).
      { chord: { key: { upArrow: true } }, intent: 'navUp', description: 'line up' },
      { chord: { key: { downArrow: true } }, intent: 'navDown', description: 'line down' },
      // Backspace deletes left (in INSERT body or the schedule field).
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { key: { delete: true } }, intent: 'backspace', description: 'delete char' },
      // Return: new line (INSERT body) or submit (schedule field).
      { chord: { key: { return: true } }, intent: 'newline', description: 'new line / submit' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'save':
          // Exit first (restores focus), then run the caller's handler — confirmMode's
          // "exit-then-act" contract so a save that opens another mode stacks correctly.
          modes.getState().exit(id);
          options.onSave();
          return;
        case 'dismiss':
          modes.getState().exit(id);
          options.onDiscard();
          return;
        case 'toggleSchedule':
          ui.scheduleFocused = !ui.scheduleFocused;
          ui.pendingD = false;
          refresh();
          return;
        case 'navUp':
          if (!ui.scheduleFocused) {
            ui.cursorLine = Math.max(clampedCursor(bodyLines()) - 1, 0);
            ui.pendingD = false;
            refresh();
          }
          return;
        case 'navDown':
          if (!ui.scheduleFocused) {
            const lines = bodyLines();
            ui.cursorLine = Math.min(clampedCursor(lines) + 1, Math.max(lines.length - 1, 0));
            ui.pendingD = false;
            refresh();
          }
          return;
        case 'backspace': {
          if (ui.scheduleFocused) {
            const current = detail().scheduleInput;
            acts().setScheduleInput(current.length > 0 ? current.slice(0, -1) : '');
            refresh();
            return;
          }
          if (ui.vimMode !== 'insert') {
            return; // Backspace is a no-op in NORMAL.
          }
          const lines = bodyLines();
          if (lines.length === 0) {
            return;
          }
          const cursor = clampedCursor(lines);
          const line = lines[cursor] ?? '';
          if (line.length > 0) {
            const next = [...lines];
            next[cursor] = line.slice(0, -1);
            writeBody(next);
          } else if (lines.length > 1 && cursor > 0) {
            // Merge with previous line on backspace at start of empty line.
            const next = [...lines];
            next.splice(cursor, 1);
            writeBody(next);
            ui.cursorLine = cursor - 1;
          }
          refresh();
          return;
        }
        case 'newline': {
          if (ui.scheduleFocused) {
            // Enter submits the schedule field (blur it).
            ui.scheduleFocused = false;
            refresh();
            return;
          }
          if (ui.vimMode !== 'insert') {
            return; // Return is a no-op in NORMAL (no command bound to it).
          }
          const lines = bodyLines();
          const cursor = clampedCursor(lines);
          const next = lines.length === 0 ? [''] : [...lines];
          next.splice(cursor + 1, 0, '');
          writeBody(next);
          ui.cursorLine = cursor + 1;
          refresh();
          return;
        }
        default:
          return intent satisfies never;
      }
    },
    // onUncaptured: the single path for printable chars + vim command letters (C12 dispatcher
    // extension). The dispatcher calls this when the declared keymap has no match. We reject
    // modified/empty keys (those are not characters we own) and route the rest by context.
    onUncaptured(input: string, key: Key): boolean {
      if (input.length === 0 || key.ctrl || key.meta || key.escape) {
        return false; // not a printable char we handle — let the dispatcher swallow it.
      }
      // Schedule field: every printable char appends to the duration string.
      if (ui.scheduleFocused) {
        acts().setScheduleInput(detail().scheduleInput + input);
        refresh();
        return true;
      }
      // INSERT: append to the body line. NORMAL: interpret as a vim command letter.
      const handled =
        ui.vimMode === 'insert' ? handleInsertChar(input) : handleNormalCommand(input);
      // Even an un-mapped NORMAL key (handled === false) is still ours to swallow: refresh so the
      // pending-'d' clear is reflected, and return true to keep exclusive capture.
      refresh();
      return handled || ui.vimMode === 'normal';
    },
    render: () => <TicketEditorSurface ui={ui} />,
  };

  return mode;
}

// ── Editor surface (pure presentation) ───────────────────────────────────────────────────────────

/**
 * The editor surface — a pure function of the `ticketDetail` slice plus the mode's `ui` closure
 * state (rule 1: no input capture here; the mode owns all input). It reads the slice for the body/
 * frontmatter and the closure for vim mode / cursor / schedule focus, and draws inside the
 * Overlay's inlayout slot so surrounding panels stay visible above.
 */
function TicketEditorSurface({ ui }: { readonly ui: EditorUiState }): JSX.Element {
  // Rule 1: read exactly the ticketDetail slice for the rendered body/frontmatter/status.
  const editedBody = useAppStore((s) => s.ticketDetail.editedBody);
  const savedBody = useAppStore((s) => s.ticketDetail.savedBody);
  const frontmatter = useAppStore((s) => s.ticketDetail.frontmatter);
  const status = useAppStore((s) => s.ticketDetail.status);
  const error = useAppStore((s) => s.ticketDetail.error);
  const scheduleInput = useAppStore((s) => s.ticketDetail.scheduleInput);
  const scheduleValid = useAppStore((s) => s.ticketDetail.scheduleValid);

  const lines = editedBody !== null ? toLines(editedBody) : [];
  const cursor = lines.length === 0 ? 0 : Math.min(ui.cursorLine, lines.length - 1);
  const hasUnsavedChanges = editedBody !== savedBody;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="blue"
      paddingX={1}
      paddingY={0}
      marginTop={1}
    >
      {/* Header: frontmatter (display-only context) */}
      <Box flexDirection="row" columnGap={2}>
        <Text bold color="blue">
          {'[editor]'}
        </Text>
        {frontmatter !== null ? (
          <EditorHeader frontmatter={frontmatter} />
        ) : (
          <Text dimColor>loading…</Text>
        )}
        {hasUnsavedChanges && <Text color="yellow">{'[modified]'}</Text>}
      </Box>

      {/* Status / error bar */}
      {status === 'error' && error !== null && <Text color="red">{`error: ${error}`}</Text>}
      {status === 'loading' && <Text dimColor>{'loading…'}</Text>}
      {status === 'saving' && <Text dimColor>{'saving…'}</Text>}

      {/* Vim mode indicator */}
      <Box flexDirection="row" columnGap={2}>
        <Text color={ui.vimMode === 'insert' ? 'green' : 'yellow'}>
          {ui.vimMode === 'insert' ? '-- INSERT --' : '-- NORMAL --'}
        </Text>
        {ui.pendingD && <Text dimColor>{'d_'}</Text>}
      </Box>

      {/* Body editor: lines with cursor highlight */}
      <Box flexDirection="column" marginTop={0} height={12}>
        {lines.length === 0 ? (
          <Text dimColor>{'(empty body)'}</Text>
        ) : (
          lines.map((line, index) => {
            const selected = index === cursor;
            const isChecklist = isChecklistLine(line);
            const checklistDone = isChecklist && /\[x\]/.test(line);
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed by design (duplicate content is normal in markdown; index IS the stable identity).
              <Box key={index} flexDirection="row">
                <Text inverse={selected && ui.vimMode === 'normal'}>{selected ? '▌' : ' '}</Text>
                {isChecklist ? (
                  checklistDone ? (
                    <Text
                      inverse={selected && ui.vimMode === 'normal'}
                      color="green"
                      dimColor={!selected}
                    >
                      {line}
                    </Text>
                  ) : (
                    <Text inverse={selected && ui.vimMode === 'normal'}>{line}</Text>
                  )
                ) : (
                  <Text inverse={selected && ui.vimMode === 'normal'} dimColor={!selected}>
                    {line}
                  </Text>
                )}
              </Box>
            );
          })
        )}
      </Box>

      {/* Schedule input (separate from body — `ticket.schedule` RPC) */}
      <Box flexDirection="row" columnGap={1} marginTop={1}>
        <Text dimColor>{'schedule:'}</Text>
        {ui.scheduleFocused ? (
          <Text color="cyan" inverse>
            {scheduleInput !== '' ? scheduleInput : ''}
            <Text color="cyan">{'█'}</Text>
          </Text>
        ) : (
          <Text dimColor>{scheduleInput !== '' ? scheduleInput : '—'}</Text>
        )}
        {scheduleInput !== '' && (
          <Text color={scheduleValid ? 'green' : 'red'}>
            {scheduleValid ? '✓' : '✗ invalid (e.g. 1d4h3m)'}
          </Text>
        )}
      </Box>

      {/* Hint bar */}
      <Box flexDirection="row" columnGap={2} marginTop={0}>
        <Text dimColor>
          {ui.vimMode === 'normal'
            ? 'j/k:nav  i:insert  x:toggle-checklist  dd:del-line  q:discard  tab:schedule  ctrl+s:save'
            : 'type text  esc:discard  ctrl+s:save'}
        </Text>
      </Box>
    </Box>
  );
}

/** The frontmatter header row — display-only context (rule 1). */
function EditorHeader({ frontmatter }: { readonly frontmatter: TicketFrontmatter }): JSX.Element {
  return (
    <>
      <Text bold>{frontmatter.title}</Text>
      {frontmatter.harness !== null && <Text dimColor>{`harness:${frontmatter.harness}`}</Text>}
      {frontmatter.model !== null && <Text dimColor>{`model:${frontmatter.model}`}</Text>}
      {frontmatter.deps !== '' && <Text dimColor>{`deps:${frontmatter.deps}`}</Text>}
      {frontmatter.worktree !== null && <Text dimColor>{`wt:${frontmatter.worktree}`}</Text>}
    </>
  );
}

// ── useTicketEditor hook ────────────────────────────────────────────────────────────────────────

/**
 * Hook for `TicketsPanel` — encapsulates the editor mode lifecycle so the panel's intent handler
 * stays at the same level of abstraction as the rest of its keymap. Returns the `openEditor`
 * callback to bind to the `'open'` intent.
 *
 * Rule 3: `open(ticketId)` goes through the store action; `saveBody()`/`schedule()` go through the
 * store actions. The component (TicketsPanel) calls `openEditor(id)` only; this hook owns the mode
 * wiring and hands the mode factory the store handle it needs to read/mutate the body slice.
 */
export function useTicketEditor(): (ticketId: string) => void {
  const { modes } = useInputStores();
  const store = useAppStoreApi();
  const openAction = useAppStore((s) => s.actions.ticketDetail.open);
  const saveBodyAction = useAppStore((s) => s.actions.ticketDetail.saveBody);
  const scheduleAction = useAppStore((s) => s.actions.ticketDetail.schedule);
  const closeAction = useAppStore((s) => s.actions.ticketDetail.close);

  return useCallback(
    (ticketId: string) => {
      // 1. Load the detail (rule 3: action is the only bus caller).
      void openAction(ticketId);
      // 2. Enter the mode — panels stay visible (inlayout). Focus saved by modeStore.
      modes.getState().enter(
        ticketEditorMode(modes, store, {
          onSave() {
            // Save the body and schedule (if valid) before closing.
            void saveBodyAction();
            void scheduleAction();
            closeAction();
          },
          onDiscard() {
            // Discard: close the slice without saving.
            closeAction();
          },
        }),
      );
    },
    [modes, store, openAction, saveBodyAction, scheduleAction, closeAction],
  );
}
