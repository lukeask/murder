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
 * ## Design (the in-layout C7M mode recipe)
 *
 * 1. **Declare a Mode** — `ticketEditorMode(modes, options)` builds a {@link Mode} with
 *    `presentation: 'inlayout'`, a keymap for save/dismiss, and a `render` thunk.
 * 2. **Enter it** — `TicketsPanel`'s `'open'` intent calls `modes.enter(ticketEditorMode(...))`.
 *    This saves the current focus; the dispatcher's layer 0 captures all keys for the mode.
 * 3. **Text input via a second `useInput`** — the mode's declared keymap handles `esc` (dismiss)
 *    and `ctrl+s` (save+dismiss). The editor surface's own `useInput` handles printable text and
 *    vim navigation. Ink supports multiple `useInput` hooks in parallel — layer 0's "swallow"
 *    only prevents lower *layers* (global chords, panel keymaps) from firing; it cannot un-call
 *    an independent `useInput`. The editor's handler ignores `esc`/`ctrl+s` (those are handled
 *    exactly once via `onIntent`) so no double-handling occurs.
 * 4. **Dismiss restores focus** — `onIntent` calls `modes.exit(id)`, the primitive's job.
 *
 * ## Vim modes
 *
 * NORMAL mode: cursor-line navigation (`j`/`k`), `i` → INSERT, `dd` → delete line,
 *   `x` → toggle checklist item on cursor line, `q` → discard (Esc also dismisses),
 *   `ctrl+s` → save (also in INSERT).
 * INSERT mode: printable chars appended to cursor line; `Backspace` → delete last char;
 *   `Return` → new line after cursor; `Esc` → back to NORMAL (Esc here means "back to normal",
 *   not "discard" — discard requires NORMAL `q` or the mode's Esc chord from NORMAL).
 *
 * ## What this is the reference for (C12 plan/note editors)
 *
 * C12 builds the plan and note editors by copying this file. The in-layout mode pattern (declare
 * Mode → enter() from panel → editor captures input → save/dismiss → focus restored) is the
 * copyable artifact. The vim editor surface below replaces `ConfirmDialog` as the render content.
 * The mode's keymap + onIntent is identical in shape to `confirmMode`; the editor adds its own
 * `useInput` for text capture.
 */

import { Box, Text, useInput } from 'ink';
import { type JSX, useCallback, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import { useInputStores } from '../hooks/useInputStores.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';

// ── Mode declaration ────────────────────────────────────────────────────────────────────────────

/** The editor mode's intent union — save or dismiss, exactly as `confirmMode`. */
type EditorIntent = 'save' | 'dismiss';

/** Stable mode id so re-entry is idempotent (the modeStore pattern). */
const EDITOR_MODE_ID = 'ticket-editor';

/** What the caller supplies to open the editor. */
export interface TicketEditorModeOptions {
  /** Called when the user saves (ctrl+s). The action layer (via `useAppStore`) does the bus call. */
  readonly onSave: () => void;
  /** Called when the user dismisses without saving (Esc from NORMAL, `q`). */
  readonly onDiscard: () => void;
}

/**
 * Build the ticket editor {@link Mode}. `presentation: 'inlayout'` keeps surrounding panels
 * visible (no `$EDITOR`-blank). The mode's keymap handles save/dismiss; the editor surface's own
 * `useInput` handles text editing. Pass the `modes` store so the mode can `exit` itself.
 *
 * C12 copies this function to build plan/note editors — swap `render` and the mode `id`.
 */
export function ticketEditorMode(
  modes: ModeStoreApi,
  options: TicketEditorModeOptions,
): Mode<EditorIntent> {
  return {
    id: EDITOR_MODE_ID,
    presentation: 'inlayout',
    // No passThrough: the editor captures everything. Panel chords do NOT fire underneath.
    keymap: [
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'discard & close' },
      { chord: { input: 's', key: { ctrl: true } }, intent: 'save', description: 'save & close' },
    ],
    onIntent(intent) {
      // Exit first (restores focus), then run the caller's handler — identical to confirmMode's
      // "exit-then-act" contract so a save that opens another mode stacks correctly.
      modes.getState().exit(EDITOR_MODE_ID);
      if (intent === 'save') {
        options.onSave();
      } else {
        options.onDiscard();
      }
    },
    render: () => <TicketEditorSurface modes={modes} options={options} />,
  };
}

// ── Editor surface ──────────────────────────────────────────────────────────────────────────────

/** Vim editing modes. NORMAL: navigate + commands; INSERT: type text. */
type VimMode = 'normal' | 'insert';

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
 * The editor surface. A pure-function-of-state component (rule 1): reads the `ticketDetail` slice
 * for the body/frontmatter to display, and dispatches `setEditedBody`/`setScheduleInput` actions
 * as the user types (rule 3 — state mutations only through actions). Renders inside the Overlay's
 * inlayout slot, so surrounding panels stay visible above.
 */
function TicketEditorSurface({
  modes,
  options,
}: {
  readonly modes: ModeStoreApi;
  readonly options: TicketEditorModeOptions;
}): JSX.Element {
  // Rule 1: read exactly the ticketDetail slice (shallow).
  const detail = useAppStore((s) => s.ticketDetail, shallow);
  // Rule 3: actions are the only view→bus path.
  const setEditedBody = useAppStore((s) => s.actions.ticketDetail.setEditedBody);
  const setScheduleInput = useAppStore((s) => s.actions.ticketDetail.setScheduleInput);

  // Local vim state: current mode and cursor line.
  const [vimMode, setVimMode] = useState<VimMode>('normal');
  const [cursorLine, setCursorLine] = useState(0);
  // Pending `d` for `dd` command detection.
  const [pendingD, setPendingD] = useState(false);
  // Schedule input focus toggle (tab to move between body and schedule field).
  const [scheduleFocused, setScheduleFocused] = useState(false);

  const lines = detail.editedBody !== null ? toLines(detail.editedBody) : [];
  const clampedCursor = lines.length === 0 ? 0 : Math.min(cursorLine, lines.length - 1);

  const updateBody = useCallback(
    (newLines: readonly string[]) => {
      setEditedBody(fromLines(newLines));
    },
    [setEditedBody],
  );

  // The editor's own useInput — handles text editing independently of the mode's keymap.
  // The mode's layer 0 swallows keys at the dispatcher level, preventing panel/global handlers
  // from firing; this handler runs in parallel and captures raw character input.
  // IMPORTANT: Esc and ctrl+s are handled via the mode's onIntent (they fire exactly once,
  // through the mode keymap). This handler ignores them to prevent double-handling.
  useInput(
    (inputChar, key) => {
      // Ignore esc and ctrl+s — those are handled by the mode's declared keymap / onIntent.
      if (key.escape || (key.ctrl && inputChar === 's')) {
        return;
      }

      // Schedule field: if focused, handle input in the duration field.
      if (scheduleFocused) {
        if (key.return) {
          // Enter submits schedule (blur the field).
          setScheduleFocused(false);
          return;
        }
        if (key.backspace || key.delete) {
          const current = detail.scheduleInput;
          setScheduleInput(current.length > 0 ? current.slice(0, -1) : '');
          return;
        }
        if (key.tab) {
          setScheduleFocused(false);
          return;
        }
        // Printable chars in schedule field.
        if (!key.ctrl && !key.meta && inputChar.length === 1) {
          setScheduleInput(detail.scheduleInput + inputChar);
          return;
        }
        return;
      }

      // Tab: toggle to schedule field.
      if (key.tab) {
        setScheduleFocused(true);
        return;
      }

      if (vimMode === 'insert') {
        // INSERT mode: printable chars, backspace, enter.
        if (key.return) {
          // New line after cursor.
          const newLines = [...lines];
          newLines.splice(clampedCursor + 1, 0, '');
          updateBody(newLines);
          setCursorLine(clampedCursor + 1);
          return;
        }
        if (key.backspace || key.delete) {
          if (lines.length === 0) {
            return;
          }
          const line = lines[clampedCursor] ?? '';
          if (line.length > 0) {
            const newLines = [...lines];
            newLines[clampedCursor] = line.slice(0, -1);
            updateBody(newLines);
          } else if (lines.length > 1 && clampedCursor > 0) {
            // Merge with previous line on backspace at start of empty line.
            const newLines = [...lines];
            newLines.splice(clampedCursor, 1);
            updateBody(newLines);
            setCursorLine(clampedCursor - 1);
          }
          return;
        }
        if (!key.ctrl && !key.meta && inputChar.length === 1) {
          const newLines = [...lines];
          const line = lines[clampedCursor] ?? '';
          newLines[clampedCursor] = line + inputChar;
          updateBody(newLines);
          return;
        }
        // Esc in INSERT → back to NORMAL (not dismiss — dismiss is NORMAL 'q' or the mode's Esc chord).
        // But the mode's keymap already handles Esc via onIntent('dismiss') — so this path never
        // fires. The editor intentionally defers to the mode for Esc (key.escape is filtered above).
        return;
      }

      // NORMAL mode commands.
      // j/k: line navigation.
      if (inputChar === 'j' || key.downArrow) {
        setCursorLine(Math.min(clampedCursor + 1, Math.max(lines.length - 1, 0)));
        setPendingD(false);
        return;
      }
      if (inputChar === 'k' || key.upArrow) {
        setCursorLine(Math.max(clampedCursor - 1, 0));
        setPendingD(false);
        return;
      }
      // i: enter INSERT mode.
      if (inputChar === 'i') {
        setVimMode('insert');
        setPendingD(false);
        return;
      }
      // x: toggle checklist item on cursor line.
      if (inputChar === 'x') {
        if (lines.length === 0) {
          return;
        }
        const line = lines[clampedCursor];
        if (line !== undefined && isChecklistLine(line)) {
          const newLines = [...lines];
          newLines[clampedCursor] = toggleChecklist(line);
          updateBody(newLines);
        }
        setPendingD(false);
        return;
      }
      // dd: delete current line.
      if (inputChar === 'd') {
        if (pendingD) {
          // Second 'd': execute delete.
          if (lines.length === 0) {
            setPendingD(false);
            return;
          }
          const newLines = [...lines];
          newLines.splice(clampedCursor, 1);
          if (newLines.length === 0) {
            newLines.push('');
          }
          updateBody(newLines);
          setCursorLine(Math.min(clampedCursor, newLines.length - 1));
          setPendingD(false);
        } else {
          setPendingD(true);
        }
        return;
      }
      // q: discard (calls onDiscard via onIntent path — we exit the mode here directly).
      if (inputChar === 'q') {
        modes.getState().exit(EDITOR_MODE_ID);
        options.onDiscard();
        setPendingD(false);
        return;
      }
      // Esc in NORMAL: already handled by mode's keymap → onIntent('dismiss').
      // Any other key clears pending 'd'.
      setPendingD(false);
    },
    // isActive: always true while the mode is up (this component only renders while the mode is active).
    { isActive: true },
  );

  const { frontmatter, status, error, scheduleInput, scheduleValid } = detail;
  const hasUnsavedChanges = detail.editedBody !== detail.savedBody;

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
          <>
            <Text bold>{frontmatter.title}</Text>
            {frontmatter.harness !== null && (
              <Text dimColor>{`harness:${frontmatter.harness}`}</Text>
            )}
            {frontmatter.model !== null && <Text dimColor>{`model:${frontmatter.model}`}</Text>}
            {frontmatter.deps !== '' && <Text dimColor>{`deps:${frontmatter.deps}`}</Text>}
            {frontmatter.worktree !== null && <Text dimColor>{`wt:${frontmatter.worktree}`}</Text>}
          </>
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
        <Text color={vimMode === 'insert' ? 'green' : 'yellow'}>
          {vimMode === 'insert' ? '-- INSERT --' : '-- NORMAL --'}
        </Text>
        {pendingD && <Text dimColor>{'d_'}</Text>}
      </Box>

      {/* Body editor: lines with cursor highlight */}
      <Box flexDirection="column" marginTop={0} height={12}>
        {lines.length === 0 ? (
          <Text dimColor>{'(empty body)'}</Text>
        ) : (
          lines.map((line, index) => {
            const selected = index === clampedCursor;
            const isChecklist = isChecklistLine(line);
            const checklistDone = isChecklist && /\[x\]/.test(line);
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed by design (duplicate content is normal in markdown; index IS the stable identity).
              <Box key={index} flexDirection="row">
                <Text inverse={selected && vimMode === 'normal'}>{selected ? '▌' : ' '}</Text>
                {isChecklist ? (
                  checklistDone ? (
                    <Text
                      inverse={selected && vimMode === 'normal'}
                      color="green"
                      dimColor={!selected}
                    >
                      {line}
                    </Text>
                  ) : (
                    <Text inverse={selected && vimMode === 'normal'}>{line}</Text>
                  )
                ) : (
                  <Text inverse={selected && vimMode === 'normal'} dimColor={!selected}>
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
        {scheduleFocused ? (
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
          {vimMode === 'normal'
            ? 'j/k:nav  i:insert  x:toggle-checklist  dd:del-line  q:discard  tab:schedule  ctrl+s:save'
            : 'type text  esc:normal  ctrl+s:save'}
        </Text>
      </Box>
    </Box>
  );
}

// ── useTicketEditor hook ────────────────────────────────────────────────────────────────────────

/**
 * Hook for `TicketsPanel` — encapsulates the editor mode lifecycle so the panel's intent handler
 * stays at the same level of abstraction as the rest of its keymap. Returns the `openEditor`
 * callback to bind to the `'open'` intent.
 *
 * Rule 3: `open(ticketId)` goes through the store action; `saveBody()` goes through the store
 * action. The component (TicketsPanel) calls `openEditor(id)` only; this hook owns the mode
 * wiring.
 */
export function useTicketEditor(): (ticketId: string) => void {
  const { modes } = useInputStores();
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
        ticketEditorMode(modes, {
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
    [modes, openAction, saveBodyAction, scheduleAction, closeAction],
  );
}
