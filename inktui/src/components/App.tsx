/**
 * App — the shell that composes the whole TUI, and the composition skeleton every future panel
 * slots into. It is the one place the providers are wired and the single {@link useRootInput} loop is
 * installed; below it, components are pure functions of their slice (rule 1) and never touch input or
 * the bus directly.
 *
 * Layout (the plan's "Approach › Layout", always-visible chrome around two toggleable regions):
 *
 *   ┌ TopBar ──────────────────────────────┐   toggled-panel labels (plans₁ … crows₀)
 *   │ ┌ left region ┐ ┌ right region ┐      │   left  visible iff any of 1–4 are on
 *   │ │ 1 2 3 4     │ │ 9 usage · 0  │      │   right visible iff 9 or 0 are on
 *   │ └─────────────┘ └──────────────┘      │   (usage sits to the left of crows)
 *   │ ChatInput (always visible)            │   the focus home — never toggled off
 *   └ BottomBar ───────────────────────────┘   contextual hints from the focused keymap
 *
 * Wiring, top to bottom:
 *  - {@link App} takes the two store bundles as props (constructed at the entrypoint with the real
 *    `BusClient`/input stores, or by a test with fakes — rule 4: the bus is injected, never imported
 *    by a component). It mounts both context providers and renders {@link Shell} inside them.
 *  - {@link Shell} runs *inside* the providers (so it may read the stores) and installs the single
 *    root input loop with {@link useRootInput}. It is the only caller of that hook.
 *
 * Region/panel pattern: each region maps its panel ids to components, rendering a panel only when it
 * is in the visible set. Every panel id now resolves to a real component (C11 filled the last
 * placeholder, `plans`, with {@link PlansPanel}); the flat {@link RosterPanel} is kept aside as the
 * copyable reference implementation. A later chunk that adds a panel swaps its `renderPanel` case for
 * a real panel copied from `RosterPanel`/`CrowsPanel` — *this file changes only at that case*. That
 * is the skeleton's contract: stable composition, panels filled in independently.
 *
 * C13: `Shell` now wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The
 * spawn handler reads the focus + app store at invocation time to derive the spawn context
 * (focused doc → reference-by-path). See {@link deriveSpawnContext} for the C11 seam note.
 */

import { Box, type DOMElement, type Key, measureElement, Text } from 'ink';
import {
  Component,
  type JSX,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { BusClient } from '../bus/BusClient.js';
import { AppStoreProvider, useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import { useBodyLayout } from '../hooks/useBodyLayout.js';
import { BusClientProvider, useBusClient } from '../hooks/useBusClient.js';
import {
  type InputStores,
  InputStoresProvider,
  useInputStores,
  useModeStore,
} from '../hooks/useInputStores.js';
import { useOrientation } from '../hooks/useOrientation.js';
import { type TerminalEvents, useRootInput } from '../hooks/useRootInput.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import type { ActionId } from '../input/bindings.js';
import { visualDown, visualUp } from '../input/chatBuffer.js';
import { expandSpans, spanIds } from '../input/chatInputStore.js';
import { reduceVimNormal } from '../input/chatVimReducer.js';
import { readClipboardImage } from '../input/clipboardImage.js';
import { BUILTIN_COMMAND_NAMES, type CommandCtx, dispatchCommand } from '../input/commandDispatch.js';
import { expandTemplates } from '../input/expandTemplates.js';
import { parseWorkflowFire } from '../input/fireWorkflow.js';
import type { ChatInputHandler } from '../input/dispatcher.js';
import { CHAT_FOCUS, type FocusId, selectEffectiveFocus } from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelId } from '../input/panels.js';
import { deriveAgentIdentity } from '../selectors/agentIdentity.js';
import {
  isChatPaneOpen,
  isFreeformChoiceSelected,
  selectActiveAgentId,
  selectConversationMeta,
  selectCycledTarget,
  selectLiveChoicePrompt,
  selectUserHistory,
} from '../selectors/conversationsSelectors.js';
import { submitCommand } from '../store/commandSubmit.js';
import { createDialogActions } from '../store/dialogs/dialogActions.js';
import { createHarnessModelsActions } from '../store/dialogs/harnessModelsActions.js';
import { createSpawnActions } from '../store/dialogs/spawnActions.js';
import { createWorktreeOptionsActions } from '../store/dialogs/worktreeOptionsActions.js';
import { DOC_DIR } from '../store/docView/docViewSlice.js';
import {
  createImageDraftStore,
  type ImageDraftStoreApi,
} from '../store/imageDraft/imageDraftStore.js';
import { murderConfirmStore } from '../store/murder/murderConfirmStore.js';
import { noteCaptureMode } from '../store/notes/noteCaptureMode.js';
import { noteCaptureStore } from '../store/notes/noteCaptureStore.js';
import type { SettingsModifier } from '../store/settings/settingsSlice.js';
import type { AppStoreApi } from '../store/store.js';
import { toastStore } from '../store/toast/toastStore.js';
import { DEFAULT_THEME_ID, PALETTES, type ThemeId } from '../theme/palettes.js';
import { setTheme } from '../theme/themeStore.js';
import { BottomBar, useBottomBarLines } from './BottomBar.js';
import { ChatInput } from './ChatInput.js';
import { CrowsPanel } from './CrowsPanel.js';
import { helpMode } from './HelpOverlay.js';
import { HistoryPanel } from './HistoryPanel.js';
import { newPlanMode } from './NewPlanModal.js';
import { newTicketMode } from './NewTicketModal.js';
import { NotesPanel } from './NotesPanel.js';
import { Overlay, presentationHidesLayout } from './Overlay.js';
import { PlansPanel } from './PlansPanel.js';
import { Rail } from './Rail.js';
import { ReportsPanel } from './ReportsPanel.js';
import { settingsMode } from './SettingsModal.js';
import type { SpawnContext } from './SpawnWizardModal.js';
import { spawnWizardMode } from './SpawnWizardModal.js';
import { Stage } from './Stage.js';
import { TicketsPanel } from './TicketsPanel.js';
import { Toast } from './Toast.js';
import { TopBar } from './TopBar.js';
import { TransitPanel } from './TransitPanel.js';
import { UsagePanel } from './UsagePanel.js';

/** The left region's panels in screen order (plan: `1` plans · `2` notes · `3` reports · `4`
 * tickets). The right region (plan: `9` usage · `0` crows — usage left of crows). One ordered list
 * per region so the shell renders panels left-to-right without re-deriving order from {@link PANELS}. */
const LEFT_PANELS: readonly PanelId[] = ['plans', 'notes', 'reports', 'tickets', 'history'];
const RIGHT_PANELS: readonly PanelId[] = ['usage', 'transit', 'crows'];

/** Pane border + padding chrome around a right-rail panel's content (mirrors USAGE_PANE_CHROME): the
 * left/right borders (2) + the default 1-cell padding each side (2) = 4. Transit's railway draws in
 * `rightRailCells − this`. */
const TRANSIT_PANE_CHROME = 4;

/**
 * The smallest terminal the shell will attempt to lay out (first-run UX: a too-small terminal gets
 * a clear notice, not a mangled frame). 60 columns is where the rails + the Stage's ≥60% floor stop
 * being co-satisfiable (and the 56–64-wide modals clamp to uselessness); 16 rows is the floor for
 * the chrome (top bar + chat + bottom bar) plus a usable body. Exported for the guard's tests.
 * Deliberately BELOW the 24×80 non-TTY fallback in {@link useTerminalSize}, so piped/CI renders
 * never trip the guard.
 */
export const MIN_TERMINAL_COLUMNS = 60;
export const MIN_TERMINAL_ROWS = 16;

/**
 * Render one panel by id — the single dispatch from a {@link PanelId} to its component. Every id
 * resolves to a real panel (`plans`, `notes`, `reports`, `tickets`, `usage`, `crows`); a future
 * chunk that adds a panel adds its `case` here, copying `RosterPanel`/`CrowsPanel`, and nothing else
 * in the shell changes. Defined as a function (not inline) so the swap is one localised edit.
 *
 * The `usageInnerWidth` (L4, R9) is threaded in so the {@link UsagePanel} sizes its fluid gauge line
 * to the width the right rail actually allots it — the budget engine ({@link useBodyLayout}) derives
 * the gauges' inner width and the Shell passes it here, the single place a layout signal reaches a
 * panel. No other panel needs it, so it's a plain extra argument rather than threading the whole
 * {@link BodyLayout} through.
 */
function renderPanel(id: PanelId, usageInnerWidth: number, rightRailCells: number): JSX.Element {
  switch (id) {
    case 'crows':
      // C9: CrowsPanel replaces the RosterPanel reference implementation here. The original
      // RosterPanel remains as the copy-reference; only this `case` changes.
      // Phase 4a: the favorited-crow chat-history panes MOVED out of here into the {@link ./Stage.js}
      // center region (they used to stack below CrowsPanel via the retired CrowChatPanel). The crows
      // panel `0` is now just the roster pane; the chat panes live in the Stage and are reached by
      // hjkl directional focus, not the crows toggle.
      return <CrowsPanel />;
    case 'plans':
      // C11: PlansPanel fills the last placeholder — parent-plan indentation + star + doc-view.
      return <PlansPanel />;
    case 'notes':
      return <NotesPanel />;
    case 'reports':
      return <ReportsPanel />;
    case 'tickets':
      return <TicketsPanel />;
    case 'history':
      return <HistoryPanel />;
    case 'transit':
      // The railway scrolls to the inner width the right rail allots it (R9): full rail width below
      // usage minus the Pane chrome, mirroring the usage landscape formula.
      return <TransitPanel innerWidth={Math.max(0, rightRailCells - TRANSIT_PANE_CHROME)} />;
    case 'usage':
      // C9: UsagePanel fills the right-region slot. Usage sits to the LEFT of crows because
      // RIGHT_PANELS = ['usage', 'crows'] (App.tsx line 59) — array order = left-to-right.
      // L4: the gauge line sizes itself to the inner width the right rail allows (R9).
      return <UsagePanel innerWidth={usageInnerWidth} />;
    default:
      return id satisfies never;
  }
}

/**
 * Derive the spawn context from the app store at `ctrl+s` invocation time. Returns a
 * {@link SpawnContext} when the **highlighted pane is the open document** (`stage:doc:<name>`), else
 * `null` (no "include this file in context" step shown in the wizard).
 *
 * ## Doc-vs-chat is decided by the EFFECTIVE FOCUS (stagelayout plan)
 * `ctrl+s` now spawns from chat OR any highlighted Stage pane (a chat-history pane or the open doc).
 * The plan's requirement is that the doc file is included ONLY when the **document** pane is the one
 * highlighted; when a chat-history pane is highlighted there is NO file prompt, even if a doc happens
 * to be open elsewhere on the Stage. So this consults `focusedId` (the effective focus passed in by
 * the spawn handler), not merely `docView.open`:
 *  - focus is `stage:doc:<name>` → return the open doc's reference-by-path context (the context step
 *    appears).
 *  - focus is a chat pane or the chat input → return `null` (no context step), regardless of whether
 *    a doc is open.
 *
 * The open doc is already shared state with a real identity (`{ kind, name }`), so panel cursors stay
 * local (rule 1) — we read the slice for the path, and the focus only gates whether to use it.
 *
 * ## Reference-by-path (locked mechanism)
 * The returned `path` is `.murder/<dir>/<name>.md` (the dir from {@link DOC_DIR}). The wizard builds
 * `"Please read ${path} before starting."` — the rogue reads the file, not an inlined body.
 */
export function deriveSpawnContext(appStore: AppStoreApi, focusedId: FocusId): SpawnContext | null {
  // The file context is included ONLY when the highlighted pane is the open doc. A chat pane / the
  // chat input never includes the file, even when a doc is open elsewhere on the Stage.
  if (!focusedId.startsWith('stage:doc:')) {
    return null;
  }
  const open = appStore.getState().docView.open;
  if (open === null) {
    return null;
  }
  const dir = DOC_DIR[open.kind];
  return { title: open.name, path: `.murder/${dir}/${open.name}.md` };
}

/**
 * Build the **persistent chat-input handler** (C11, part F) over the chat buffer store + the app
 * store. This is the layer-2 chat short-circuit's consumer (see {@link ../input/dispatcher.js}'s
 * {@link ChatInputHandler}): chat is the permanent focus home, so it is NOT a transient mode — the
 * buffer lives in `chatInput` and this handler edits it on each keystroke and sends on Enter.
 *
 * Routing (rule 3): on Enter, the active agent id is derived from the conversations + roster slices
 * via {@link selectActiveAgentId} (the discriminated-union identity — no conversation-id parsing),
 * and the message is sent through `actions.conversations.send` (the sole bus caller). The buffer is
 * cleared after dispatch. An empty buffer or no active agent makes Enter a no-op (still consumed, so
 * the dispatcher reports handled — Enter belongs to the chat field).
 *
 * Pure factory over the two stores: no React, exported so a test drives the keystroke→send path
 * directly. Global ctrl-chords never reach here (layer 1 preempts layer 2), so this can treat every
 * event it sees as chat text — it returns `false` only for control keys it does not own (so an
 * unhandled control key isn't falsely reported as consumed).
 */
/**
 * Map one chat key event to the tmux key to forward to a live choice dialog (the multiple-choice
 * takeover). Named keys (`Up`, `Enter`, `Escape`, `Space`, `BSpace`, …) drive the dialog cursor /
 * toggle / confirm; printable characters forward as literal text so the dialog's inline "type
 * something" field works. Returns `null` for events the takeover does not own (e.g. ctrl-chords),
 * which then fall through unhandled. Exported for unit tests.
 */
export function choiceKeyFor(
  input: string,
  key: Key,
): { readonly key: string; readonly literal: boolean } | null {
  if (key.upArrow === true) return { key: 'Up', literal: false };
  if (key.downArrow === true) return { key: 'Down', literal: false };
  if (key.leftArrow === true) return { key: 'Left', literal: false };
  if (key.rightArrow === true) return { key: 'Right', literal: false };
  if (key.tab === true) return { key: key.shift === true ? 'BTab' : 'Tab', literal: false };
  if (key.return === true) return { key: 'Enter', literal: false };
  if (key.escape === true) return { key: 'Escape', literal: false };
  if (key.backspace === true || key.delete === true) return { key: 'BSpace', literal: false };
  if (input === ' ') return { key: 'Space', literal: false };
  if (input.length > 0 && key.ctrl !== true && key.meta !== true) {
    return { key: input, literal: true };
  }
  return null;
}

/**
 * The chat box content width in cells, for {@link ../input/chatBuffer.js layout}/visualUp/Down. Read
 * from the chat focusable's measured rect (`useMeasureFocus(CHAT_FOCUS)` records it in the focus
 * store): content width = rect width − 2 (round border) − 2 (paddingX:1 both sides). Falls back to a
 * `process.stdout.columns`-derived width (cols − 4) when the rect is unmeasured (boot, non-TTY), with
 * an 80→76 default when even that is unknown. Always ≥1 so `layout` is well-defined.
 */
function chatContentWidth(focus: InputStores['focus']): number {
  const rect = focus.getState().rects.get(CHAT_FOCUS);
  const fromRect = rect !== undefined && rect.width > 0 ? rect.width - 4 : 0;
  if (fromRect >= 1) {
    return fromRect;
  }
  const cols = process.stdout.columns ?? 80;
  return Math.max(1, cols - 4);
}

export function makeChatInputHandler(
  chatInput: InputStores['chatInput'],
  appStore: AppStoreApi,
  imageDraft: ImageDraftStoreApi,
  commandCtx: CommandCtx,
  chatHistory: InputStores['chatHistory'],
  chatVim: InputStores['chatVim'],
  focus: InputStores['focus'],
): ChatInputHandler {
  /** Record a just-sent message into the murder-wide history ring and reset history-nav. Called at
   * every send boundary (conversations.send AND dispatchCommand text-send). */
  const recordSend = (message: string): void => {
    chatHistory.getState().record(message);
    // The buffer is cleared by the send path; clearing also resets historyIndex/stashedDraft, so the
    // next `up` starts fresh from the newest entry.
  };

  /** Apply a vim effect (from {@link ../input/chatVimReducer.js reduceVimNormal}) to the chat + vim
   * stores. The reducer returns the pending operator to write back (it does NOT hold it), so every
   * branch sets/clears pending explicitly. (Per the spec, `cw`/`cc` do not populate the register —
   * the reducer's enterInsert effect carries no slice; accepted for v1.) */
  const applyVimEffect = (effect: ReturnType<typeof reduceVimNormal>): void => {
    const cin = chatInput.getState();
    const vim = chatVim.getState();
    switch (effect.kind) {
      case 'buffer':
        cin.setBuffer(effect.state);
        vim.setPending(null);
        break;
      case 'enterInsert':
        cin.setBuffer(effect.state);
        vim.setSubmode('insert');
        vim.setPending(null);
        break;
      case 'setRegister':
        cin.setBuffer(effect.state);
        vim.setRegister(effect.register);
        vim.setPending(null);
        break;
      case 'paste':
        cin.setBuffer(effect.state);
        vim.setPending(null);
        break;
      case 'pending':
        vim.setPending(effect.pending);
        break;
      case 'none':
        break;
    }
  };

  return {
    handleKey(input, key): boolean {
      // Multiple-choice takeover: when the active target's transcript ends in a LIVE choice_prompt
      // (a CC AskUserQuestion / trust dialog), the chat input belongs to the dialog — keys forward
      // to the agent's pane via `agent.send_key` (rule 3: through the conversations action). The
      // pane is ground truth; the parser's block-updated events move the rendered cursor. Checked
      // FIRST so Enter answers the dialog rather than sending the buffer.
      {
        const state = appStore.getState();
        const agentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
        if (agentId !== null) {
          const livePrompt = selectLiveChoicePrompt(state.conversations, agentId);
          if (livePrompt !== null) {
            // Freeform "Type something." takeover: edit a LOCAL buffer (instant echo, no per-key
            // round-trip) and flush the whole answer on Enter as ONE ordered literal send. Routing
            // every keystroke through `agent.send_key` was slow (one tmux round-trip per char) and
            // reordered the text under fast typing — the async sends raced. The local buffer reuses
            // the chat field (free during the takeover); the same predicate drives the render.
            if (isFreeformChoiceSelected(livePrompt)) {
              if (key.return === true) {
                // One literal send of the full answer + a newline submits CC's inline field — the
                // exact pattern the `/` passthrough uses (atomic, ordered).
                const buffer = chatInput.getState().text;
                void state.actions.conversations.sendKey(agentId, `${buffer}\n`, true);
                chatInput.getState().clear();
                return true;
              }
              if (key.backspace === true || key.delete === true) {
                chatInput.getState().backspace();
                return true;
              }
              // Printable (incl. space and digits) → local echo. Checked before the nav fallback so a
              // space lands in the buffer instead of moving the dialog cursor.
              if (
                input.length > 0 &&
                key.ctrl !== true &&
                key.meta !== true &&
                key.escape !== true
              ) {
                chatInput.getState().append(input);
                return true;
              }
              // Navigation / cancel leaves the field: drop the local draft and forward the key so the
              // live pane moves the cursor / switches question tab / cancels.
              chatInput.getState().clear();
              const navForward = choiceKeyFor(input, key);
              if (navForward !== null) {
                void state.actions.conversations.sendKey(
                  agentId,
                  navForward.key,
                  navForward.literal,
                );
              }
              return true; // consume: stay inside the dialog rather than leaking to chat send
            }
            const forward = choiceKeyFor(input, key);
            if (forward === null) {
              return false;
            }
            void state.actions.conversations.sendKey(agentId, forward.key, forward.literal);
            return true;
          }
          // Queued-message "send now": with a held message for the target, an Enter on an EMPTY
          // buffer interrupts the agent — the service then delivers the queued message at the next
          // input-ready parse. A non-empty buffer keeps normal send semantics (the new text appends
          // to the queue server-side).
          if (key.return === true && chatInput.getState().text.length === 0) {
            const queued = selectConversationMeta(state.conversations, agentId).queuedMessage;
            if (queued !== null) {
              void state.actions.conversations.interrupt(agentId);
              return true;
            }
          }
        }
      }
      // shift+enter → insert a newline at the cursor (user ask #1). Plain Enter still sends (below).
      // Checked before the send branch so a held Shift never submits. Consumed.
      if (key.return === true && key.shift === true) {
        chatInput.getState().insert('\n');
        return true;
      }
      // Enter → send the buffer to the active agent, then clear. Always consumed (Enter is chat's).
      //
      // F9 submit-while-uploading policy (per-state, because the states differ by *path availability*
      // — the full path only comes back from the server on resolve):
      //  - any span still `uploading` → BLOCK the send (no path yet to expand): info toast, keep the
      //    buffer intact so nothing is lost. Least-surprising: the user just waits a beat.
      //  - `done` spans  → expanded to `![image]({path})` via `expandSpans`.
      //  - `failed` spans → STRIPPED from the outgoing markdown (their id is absent from `pathsById`),
      //    so a failed upload never traps the buffer; the in-text marker the user saw is simply dropped.
      if (key.return === true) {
        const buffer = chatInput.getState().text;
        if (buffer.length > 0) {
          const draftState = imageDraft.getState();
          const ids = spanIds(buffer);
          const stillUploading = ids.some((id) => draftState.drafts[id]?.status === 'uploading');
          if (stillUploading) {
            // Block: leave the buffer untouched and tell the user why (consumed — Enter is chat's).
            toastStore.getState().push('image still uploading…', { ttlMs: 4000 });
            return true;
          }
          let message = expandSpans(buffer, draftState.pathsById());
          // Workflow firing (Chunk E): a leading `:name` matching a SAVED workflow FIRES it instead of
          // being sent/expanded as chat. Runs AFTER expandSpans but BEFORE expandTemplates so the locked
          // precedence holds: builtin > workflow > template > literal (a builtin name returns null here
          // and is handled later by dispatchCommand; a same-named template only expands if no fire).
          const wfNames = new Set(appStore.getState().workflows.items.map((w) => w.name));
          const fire = parseWorkflowFire(message, BUILTIN_COMMAND_NAMES, wfNames);
          if (fire !== null) {
            void appStore.getState().actions.workflows.run(fire.name, fire.args);
            // Fired — do NOT expand/dispatch/send this buffer as chat. Leave the input in the same clean
            // state a normal send does: drop the drafts (they're consumed) and clear the buffer (the
            // shared tail below also clears, but firing returns early, so do it here).
            for (const id of ids) {
              imageDraft.getState().drop(id);
            }
            chatInput.getState().clear();
            return true;
          }
          // Template expansion (leading `:name args` fill / inline `:name:` macros), upstream of the
          // prefix dispatcher so a builtin `:command` still wins and an unknown `:foo` falls through.
          const templateRegistry = new Map<string, string>(
            appStore.getState().templates.items.map((t) => [t.name, t.body]),
          );
          message = expandTemplates(message, templateRegistry, BUILTIN_COMMAND_NAMES);
          if (message.length > 0) {
            const agentId = selectActiveAgentId(
              appStore.getState().conversations,
              appStore.getState().roster,
              appStore.getState().favorites,
            );
            // Prefix dispatcher (Workstream E): `/` → harness passthrough, `:` → murder command,
            // anything else → false (fall through to the normal send below). Routed in ONE place.
            if (!dispatchCommand(message, agentId, commandCtx)) {
              if (agentId !== null) {
                void appStore.getState().actions.conversations.send(agentId, message);
                // Send boundary (user ask #4): record the sent message in the murder-wide recall ring.
                // `clear()` below resets history-nav (historyIndex=null, stashedDraft=null).
                recordSend(message);
              }
            }
          }
          // Drop the drafts now that they're expanded/stripped — the buffer is clearing.
          for (const id of ids) {
            imageDraft.getState().drop(id);
          }
        }
        chatInput.getState().clear();
        return true;
      }
      // ctrl+v → image paste. Read the clipboard client-side; if it holds an image, mint a draft +
      // wrap a span into the buffer (the label→file binding is known instantly — see imageDraftStore).
      // If no image, decline (return false): plain-text paste is the terminal's job, not ours.
      if (key.ctrl === true && input === 'v') {
        void readClipboardImage().then((image) => {
          if (image === null) {
            return;
          }
          const id = imageDraft.getState().paste(image.bytes, image.ext);
          chatInput.getState().appendImageSpan(id);
        });
        return true;
      }
      // VIM MODE (user ask #3): when enabled, route normal-mode keys through the reducer; insert mode
      // behaves like the non-vim text field plus Esc→normal. Toggling vim on starts in NORMAL (the
      // store's initial submode), so the first keystroke after enabling is a command, not text.
      const vimOn = appStore.getState().settings.vimMode;
      if (vimOn) {
        const vimState = chatVim.getState();
        if (vimState.submode === 'normal') {
          // j/k are VISUAL up/down (the reducer is width-agnostic and returns LOGICAL motion); when no
          // operator is pending we remap them to chatBuffer.visualUp/visualDown at the known width.
          if (vimState.pending === null && (input === 'j' || input === 'k')) {
            const width = chatContentWidth(focus);
            const buf = chatInput.getState().buffer;
            const moved = input === 'k' ? visualUp(buf, width) : visualDown(buf, width);
            if (moved !== null) {
              chatInput.getState().setBuffer(moved);
            }
            return true;
          }
          const effect = reduceVimNormal(
            chatInput.getState().buffer,
            input,
            key,
            vimState.pending,
            vimState.register,
          );
          applyVimEffect(effect);
          return true;
        }
        // submode === 'insert': Esc → normal. Everything else falls through to the shared text-entry
        // logic below (insert/backspace/arrows behave exactly like non-vim).
        if (key.escape === true) {
          chatVim.getState().setSubmode('normal');
          return true;
        }
      }

      // --- Shared text-entry logic (non-vim, and vim INSERT mode) ---
      const width = chatContentWidth(focus);
      // Horizontal motion + line motion.
      if (key.leftArrow === true) {
        chatInput.getState().moveLeft();
        return true;
      }
      if (key.rightArrow === true) {
        chatInput.getState().moveRight();
        return true;
      }
      // Up → visual-up; on the TOP visual row (visualUp null) recall older history.
      if (key.upArrow === true) {
        const moved = visualUp(chatInput.getState().buffer, width);
        if (moved !== null) {
          chatInput.getState().setBuffer(moved);
        } else {
          chatInput.getState().historyPrev(chatHistory.getState().entries);
        }
        return true;
      }
      // Down → visual-down; on the BOTTOM visual row (visualDown null) walk forward through history
      // (restoring the stashed live draft at the newest end).
      if (key.downArrow === true) {
        const moved = visualDown(chatInput.getState().buffer, width);
        if (moved !== null) {
          chatInput.getState().setBuffer(moved);
        } else {
          chatInput.getState().historyNext(chatHistory.getState().entries);
        }
        return true;
      }
      // Backspace → delete before the cursor; Delete → delete at the cursor. A whole image span is
      // removed at its edge and its id returned, so we drop its imageDraftStore entry. Consumed.
      if (key.backspace === true) {
        const removedId = chatInput.getState().backspace();
        if (removedId !== null) {
          imageDraft.getState().drop(removedId);
        }
        return true;
      }
      if (key.delete === true) {
        const removedId = chatInput.getState().deleteForward();
        if (removedId !== null) {
          imageDraft.getState().drop(removedId);
        }
        return true;
      }
      // A printable character (no modifier, single visible char) → insert at the cursor. Consumed.
      // Reject empty/modified input: those are control keys the chat field doesn't own (return
      // `false` so the dispatcher doesn't falsely report them handled).
      if (input.length > 0 && key.ctrl !== true && key.meta !== true && key.escape !== true) {
        chatInput.getState().insert(input);
        return true;
      }
      return false;
    },
  };
}

/**
 * A render-path error boundary for the Body region (the one structural gap flagged in review). A
 * throw inside any panel's render (a selector returning a malformed view, an undefined-index access
 * in a `renderEntry`) would otherwise take down the entire Ink app — Ink has no per-subtree recovery
 * of its own. Wrapping just the Body converts that into a degraded panel: the chrome (TopBar, chat,
 * BottomBar) stays live and the body shows a "panel crashed" line instead of the process exiting.
 *
 * It must be a class component — `componentDidCatch`/`getDerivedStateFromError` have no hooks
 * equivalent. The fallback uses bare Ink primitives (no `useTheme`, which is a hook) and a `dimColor`
 * notice, matching the min-terminal guard's styling. Reset is intentionally manual (restart/resize);
 * a silently self-resetting boundary would just re-throw on the next render of the same bad slice.
 */
class BodyErrorBoundary extends Component<{ readonly children: ReactNode }, { hasError: boolean }> {
  override state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  override render(): ReactNode {
    if (this.state.hasError) {
      return (
        <Box flexGrow={1} alignItems="center" justifyContent="center" flexDirection="column">
          <Text>a panel crashed</Text>
          <Text dimColor>the rest of the app is still live — restart to recover</Text>
        </Box>
      );
    }
    return this.props.children;
  }
}

/**
 * The shell body — runs inside both providers so it can read the stores. Installs the one root input
 * loop, then lays out the always-visible chrome (top bar, chat input, bottom bar) around the
 * orientation-aware Body.
 *
 * ## Orientation-aware Body (Phase 2)
 * The Body is `[ Rail(left) · Stage · Rail(right) ]`, laid out along an axis chosen by the single
 * {@link useOrientation} call here (one source of truth, threaded to both Rails so they never
 * diverge mid-tree — see the hook's handoff note):
 *  - landscape → Body is a `row`: the rails sit on the left/right of a center Stage (each Rail then
 *    stacks its own panels in a column);
 *  - portrait  → Body is a `column`: the rails stack above/below the Stage (each Rail lays its own
 *    panels out in a row).
 * Each Rail collapses to nothing when it has no visible panels, so the Stage grows to fill whatever
 * the rails leave (full width when both rails are off).
 *
 * ## Stage slot (Phase 4a)
 * The center hosts the {@link ./Stage.js Stage}: the favorited-crow chat-history Panes, each a
 * focusable Stage pane reachable by `alt+h/j/k/l`. It grows to fill whatever the rails leave (full
 * width when both rails are off) and clips its own overflow. Phase 4b adds open-document panes to the
 * Stage's right; the `docView` slice is untouched here.
 *
 * C13: wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The handler reads the
 * app store at invocation time (not during render) so it always sees current state.
 *
 * `ctrl+s` spawns from chat OR a highlighted Stage pane (a chat-history pane or the open doc); the
 * dispatcher routes it (declines on a list panel, where alt+f stays the star chord). The doc file is
 * included in the spawn context ONLY when the highlighted pane is the document — see
 * {@link deriveSpawnContext}, which reads the effective focus. `ctrl+q` closes the highlighted Stage
 * pane (see `closePaneHandler`). C11 also loads the persisted favorites once on mount via the favorites
 * action.
 */
function Shell({
  project,
  terminalEvents,
}: {
  readonly project?: string | undefined;
  /** The kitty stdin shim's chord channel (Phase 2), passed from the live entrypoint like `bus`. The
   * root input loop subscribes to it; omitted in smoke/tests (no shim → no side-channel chords). */
  readonly terminalEvents?: TerminalEvents | undefined;
}): JSX.Element {
  const { modes, chatInput, chatHistory, chatVim, bindings, keymaps, focus } = useInputStores();
  const appStore = useAppStoreApi();
  const bus = useBusClient();
  const loadFavorites = useAppStore((s) => s.actions.favorites.load);
  const loadSettings = useAppStore((s) => s.actions.settings.load);
  // Live terminal size — `rows` bounds the root box so the frame always fits one screen (see the
  // return); `columns` feeds the min-terminal-size guard below.
  const { rows, columns } = useTerminalSize();
  // The ONE orientation read (rule: one source of truth) — threaded to both Rails and the Body axis.
  const orientation = useOrientation();
  // The user-configured inter-pane-border gap (settings: "Pane gap", 0–4). One read here, threaded
  // to the budget engine (so the Stage floor accounts for it) AND down to the Body box / Stage / Rails
  // as their `columnGap`/`rowGap` — the single source of truth for inter-pane spacing (mirrors the
  // single orientation read). `0` = flush borders (the default).
  const paneGap = useAppStore((s) => s.settings.paneGap);
  // L4c-fix2: portrait budgets the rows axis, so it must know the height the Body region actually
  // occupies = `rows − topbar − ChatInput − footer`. Two of those are MEASURED and one is COMPUTED:
  //   • topbar + ChatInput — measured via `measureElement` on their `flexShrink={0}` boxes. They are
  //     stable (no wrap), so Yoga's height matches what they draw; ChatInput is measured (not
  //     hardcoded) because it grows with image-draft/attachment rows.
  //   • footer (BottomBar) — COMPUTED, not measured. Ink reports a wrapped flex-row / percentage-width
  //     Text as 1 line even when the terminal draws 2, so `measureElement` on the footer is unreliable
  //     (it returned 4 for a 5-row chrome at 78×50, making the Body 1 row too tall and the bottom rail
  //     strip clip its border into the chat input). Instead the footer packs its hints into N explicit
  //     single-line rows; `useBottomBarLines().length` is that exact N, shared with the BottomBar
  //     render so the count and the height accounting can never disagree.
  // The `useLayoutEffect` is GUARDED (writes only on a real change) so it settles in one extra render
  // and never loops. Before the first measurement the heights are 0 → `bodyHeight` is 0 →
  // `useBodyLayout` falls back to terminal `rows` and self-corrects (non-TTY tests report 0 and stay
  // on the fallback). Landscape ignores this height entirely (it budgets the width axis).
  const topbarRef = useRef<DOMElement | null>(null);
  const chatInputRef = useRef<DOMElement | null>(null);
  const [topbarHeight, setTopbarHeight] = useState(0);
  const [chatInputHeight, setChatInputHeight] = useState(0);
  useLayoutEffect(() => {
    if (topbarRef.current !== null) {
      const { height } = measureElement(topbarRef.current);
      if (height !== topbarHeight) {
        setTopbarHeight(height);
      }
    }
    if (chatInputRef.current !== null) {
      const { height } = measureElement(chatInputRef.current);
      if (height !== chatInputHeight) {
        setChatInputHeight(height);
      }
    }
  });
  // Footer row count (computed — see above). Shared with the BottomBar render via the same hook.
  const footerLines = useBottomBarLines().length;
  // The Body's true available height = terminal rows − topbar − ChatInput − footer rows. Only
  // meaningful once topbar + ChatInput have been measured (>0); before that it is 0 and `useBodyLayout`
  // falls back to the terminal `rows` (self-correcting on the next layout). `max(0, …)` so a transient
  // over-measure can never make the portrait total negative.
  const bodyHeight =
    topbarHeight > 0 && chatInputHeight > 0
      ? Math.max(0, rows - topbarHeight - chatInputHeight - footerLines)
      : 0;
  // The responsive cell budget (R1–R7): explicit rail widths/heights + the Stage's ≥60% floor,
  // computed from the live terminal size, orientation, and each rail's natural content width. One
  // call here, threaded down to both Rails and the Stage — the single source of truth for the Body's
  // sizing, mirroring the single `useOrientation()` read. Portrait budgets against the MEASURED Body
  // height (L4c) so nothing overflows into the chrome; landscape is unaffected (budgets width).
  const bodyLayout = useBodyLayout(bodyHeight, paneGap);
  // The {@link PanelId} → component dispatch, closing over the current usage inner width (L4/R9) so
  // the UsagePanel sizes its fluid gauge line to the width its right rail supports. Memoised on that
  // width so a Rail (which is `memo`-free but cheap) only re-derives when it actually changes, not on
  // every Body re-render. Passed to BOTH Rails; only the usage case reads the width.
  const dispatchPanel = useMemo(
    () =>
      (id: PanelId): JSX.Element =>
        renderPanel(id, bodyLayout.usageInnerWidth, bodyLayout.rightRailCells),
    [bodyLayout.usageInnerWidth, bodyLayout.rightRailCells],
  );

  // F9: the image-draft store owns the `image.upload` bus call + the FIFO upload queue (it writes a
  // file, doesn't mutate a conversation — so rule 3's "send lives in conversations" doesn't apply).
  // One instance per bus; toasts go to the app singleton. Stable across renders (the bus is stable).
  const imageDraft = useMemo(() => createImageDraftStore(bus, toastStore), [bus]);

  // Load persisted favorites once on mount (rule 3: via the action). Fire-and-forget; a rejection
  // (e.g. the modeled-not-live prefs RPC against a real bus) lands in the slice's error and leaves
  // favorites at their defaults — never crashes the shell.
  useEffect(() => {
    void loadFavorites();
  }, [loadFavorites]);

  // Phase 3: load persisted settings once on mount, next to favorites (rule 3: via the action).
  // Same fire-and-forget contract — a rejection lands in the slice's error and leaves settings at
  // their defaults (alt modifier, no rebinds), never crashing the shell.
  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  // Chat-input overhaul (user ask #4): seed the murder-wide send-history recall ring from the
  // authoritative conversations snapshot, and re-seed whenever the transcripts ref-change (a fresh
  // snapshot on reconnect, or live user blocks arriving). `seed` replaces the ring wholesale and is
  // self-healing against the optimistic `record` the handler does at each send. Primed once at mount
  // from the current slice, then on every transcripts change.
  useEffect(() => {
    chatHistory.getState().seed(selectUserHistory(appStore.getState().conversations));
    return appStore.subscribe((state, prev) => {
      if (state.conversations.transcripts !== prev.conversations.transcripts) {
        chatHistory.getState().seed(selectUserHistory(state.conversations));
      }
    });
  }, [appStore, chatHistory]);

  // Phase 3/5: the settings → store bridges. The settings slice owns the persisted preferences; the
  // input layer's bindingsStore and the global themeStore must stay bus-free (neither knows the bus
  // exists), so this one subscription mirrors the relevant fields onto them whenever they change. It
  // runs once at mount (priming both stores from the loaded settings) and then on every settings
  // change (the optimistic `update` path → instant dispatcher/keymap/footer/theme reaction).
  //
  // Phase 5: the theme bridge. The slice stores `theme` as an opaque string (server authority); we
  // validate it against the known PALETTES before applying — an unknown id (a stale/foreign config)
  // falls back to the default scheme so a bad value can never leave the UI uncolored.
  useEffect(() => {
    const syncBindings = (
      modifier: SettingsModifier,
      keyOverrides: Record<string, string>,
    ): void => {
      const bindingsState = bindings.getState();
      bindingsState.setModifier(modifier);
      // The slice stores keyOverrides opaquely (string keys); the bindings store narrows them onto
      // the ActionId union. A stray non-ActionId key is harmless (resolveBindings ignores it).
      bindingsState.setOverrides(keyOverrides as Partial<Record<ActionId, string>>);
    };
    const syncTheme = (theme: string): void => {
      // Validate against the known palette ids; unknown → default (never an uncolored UI).
      // Commit path: push the persisted settings.theme (source of truth) → themeStore. SettingsModal
      // also calls setTheme directly for transient live preview — see themeStore.ts's source-of-truth note.
      const id: ThemeId = theme in PALETTES ? (theme as ThemeId) : DEFAULT_THEME_ID;
      setTheme(id);
    };
    const current = appStore.getState().settings;
    syncBindings(current.modifier, current.keyOverrides as Record<string, string>);
    syncTheme(current.theme);
    return appStore.subscribe((state, prev) => {
      if (
        state.settings.modifier !== prev.settings.modifier ||
        state.settings.keyOverrides !== prev.settings.keyOverrides
      ) {
        syncBindings(
          state.settings.modifier,
          state.settings.keyOverrides as Record<string, string>,
        );
      }
      if (state.settings.theme !== prev.settings.theme) {
        syncTheme(state.settings.theme);
      }
    });
  }, [appStore, bindings]);

  // `ctrl+s` → open the spawn wizard (fires when chat OR a highlighted Stage pane is focused; see
  // dispatcher.ts). Reads the store imperatively at call time (getState()) so no stale closure; stores
  // are stable references.
  const spawnHandler = (): void => {
    // Doc-vs-chat file context is decided by the effective focus (stagelayout plan): include the doc
    // file ONLY when the highlighted pane is the open doc; a highlighted chat pane / the chat input
    // gets no file prompt, even if a doc is open elsewhere on the Stage.
    const spawnContext = deriveSpawnContext(appStore, selectEffectiveFocus(focus));
    const actions = createSpawnActions(bus, appStore);
    const modelActions = createHarnessModelsActions(bus);
    const worktreeActions = createWorktreeOptionsActions(bus);
    modes
      .getState()
      .enter(spawnWizardMode(modes, actions, { spawnContext, modelActions, worktreeActions }));
  };

  // `alt+o` / `ctrl+o` → open the settings modal. Reads the persisted slice at call time so the modal opens
  // reflecting the live preferences; commits route back through `actions.settings.update`. The slice
  // stores `theme`/`keyOverrides` opaquely, so we narrow them onto the modal's typed shape here (an
  // unknown theme falls back to the default, mirroring the theme bridge above).
  const openSettingsHandler = (): void => {
    const settings = appStore.getState().settings;
    const settingsActions = appStore.getState().actions.settings;
    const theme: ThemeId =
      settings.theme in PALETTES ? (settings.theme as ThemeId) : DEFAULT_THEME_ID;
    modes.getState().enter(
      settingsMode(modes, settingsActions, {
        modifier: settings.modifier,
        theme,
        paneGap: settings.paneGap,
        vimMode: settings.vimMode,
        startupRogue: settings.startupRogue,
        keyOverrides: settings.keyOverrides as Record<string, string>,
        collaboratorHarness: settings.collaboratorHarness,
        effectiveCollaborator: settings.effectiveCollaboratorHarness,
        crowHarnesses: settings.crowHarnesses,
        effectiveCrow: settings.effectiveCrowHarnesses,
        llm: settings.llm,
        llmEnv: settings.llmEnv,
      }),
    );
  };

  // `super+p` → open the new-plan single-form wizard (item 3). On success: toast + open the plan's doc
  // pane (the auto path returns the FINAL name). Reads stores at call time (no stale closure).
  const newPlanHandler = (): void => {
    const actions = createDialogActions(bus);
    const docViewActions = appStore.getState().actions.docView;
    modes.getState().enter(
      newPlanMode(modes, actions, {
        onSubmit(planName) {
          toastStore.getState().push(`plan "${planName}" created`, { ttlMs: 6000 });
          void docViewActions.open('plan', planName);
        },
      }),
    );
  };

  // `ctrl+t` → open the new-ticket single-form modal (BUG 1). Mirrors `newPlanHandler`: builds the
  // dialog actions at call time and enters the modal mode; on success pushes a toast. The ticket id is
  // delivered by the action but we only surface the title in the toast.
  const newTicketHandler = (): void => {
    const actions = createDialogActions(bus);
    modes.getState().enter(
      newTicketMode(modes, actions, {
        onSubmit(_ticketId, title) {
          toastStore.getState().push(`ticket "${title}" created`, { ttlMs: 6000 });
        },
      }),
    );
  };

  // `ctrl+n` → open the quick-note capture (item 10). Draft persists across cancel/reopen (the mode
  // resets the FSM only on a confirmed submit); submit is fire-and-forget via `notetaker.capture.submit`
  // (close instantly + toast). Title is auto/LLM (empty title field).
  const quickNoteHandler = (): void => {
    modes.getState().enter(
      noteCaptureMode(modes, noteCaptureStore, {
        onSubmit(draft, title) {
          void submitCommand(bus, 'notetaker.capture.submit', {
            raw: draft,
            ...(title !== undefined && title.trim() !== '' ? { title: title.trim() } : {}),
          }).catch((error: unknown) => {
            const message = error instanceof Error ? error.message : String(error);
            toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
          });
          toastStore.getState().push('note captured', { ttlMs: 6000 });
        },
        onCancel() {},
      }),
    );
  };

  // Item 12: `?` → open the keybinding help overlay. Reads the live resolved bindings + keymap
  // registry at call time so the overlay reflects the current modifier/rebinds and the panels that
  // are actually registered.
  const keyHelpHandler = (): void => {
    modes.getState().enter(helpMode(modes, bindings.getState().resolved, keymaps));
  };

  // Item 9 super-chords: cycle the chat target (prev/−1, next/+1) through EVERY chattable crow
  // (spec order — {@link selectCycleTargets}). Cycling is a pure input-routing change: it sets the
  // send target but does NOT add the crow's chat box to the Stage — the user opens a pane explicitly
  // with `toggleTargetPane` (ctrl+w). Reads the store imperatively so it always sees current state.
  const cycleTarget = (direction: 1 | -1): void => {
    const state = appStore.getState();
    const result = selectCycledTarget(
      state.conversations,
      state.roster,
      state.favorites,
      direction,
    );
    if (result === null) {
      return;
    }
    state.actions.conversations.setActivePaneAgentId(result.agentId);
  };

  // Item 9 super-chord: toggle the current chat target's pane from the chat box.
  const toggleTargetPaneHandler = (): void => {
    const state = appStore.getState();
    const agentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
    if (agentId === null) {
      return;
    }
    // `toggleChatPane` needs the current open state (it writes the opposite override). Derive it via
    // the agent's identity so the kind-default favorite is honoured for an un-overridden pane.
    const row = state.roster.rows.find((r) => r.agentId === agentId);
    const identity = row === undefined ? null : deriveAgentIdentity(row);
    const currentlyOpen =
      identity === null
        ? state.conversations.paneOverrides.get(agentId) === true
        : isChatPaneOpen(identity, state.favorites, state.conversations.paneOverrides);
    state.actions.conversations.toggleChatPane(agentId, currentlyOpen);
  };

  // ctrl+m murder chord. ARM resolves the targeted crow from the live UI state: the focused chat
  // pane's crow when a `stage:chat:` pane holds focus, else the active chat target (the crow the
  // user is chatting to). The crows-panel case never reaches this handler — the dispatcher declines
  // there so the panel arms with its own local cursor row. The confirm/cancel handlers drive the
  // shared {@link murderConfirmStore}, so the panel-armed and shell-armed paths confirm identically.
  const murderHandler = (): void => {
    const state = appStore.getState();
    const effective = selectEffectiveFocus(focus);
    const agentId = effective.startsWith('stage:chat:')
      ? effective.slice('stage:chat:'.length)
      : selectActiveAgentId(state.conversations, state.roster, state.favorites);
    if (agentId === null) {
      toastStore.getState().push('no crow to murder', { ttlMs: 4000 });
      return;
    }
    const row = state.roster.rows.find((r) => r.agentId === agentId);
    const identity = row === undefined ? null : deriveAgentIdentity(row);
    murderConfirmStore.getState().arm({ agentId, name: identity?.label ?? agentId });
  };
  const murderConfirmHandler = (): void => {
    const pending = murderConfirmStore.getState().pending;
    murderConfirmStore.getState().clear();
    if (pending === null) {
      return;
    }
    // `agent.stop` is the live orchestrator kill (orchestrator_worker.py). Fire-and-forget with the
    // outcome as a toast — the roster row update arrives via the `agent` entity snapshot.
    void submitCommand(bus, 'agent.stop', { agent_id: pending.agentId })
      .then(() => {
        toastStore.getState().push(`murdered ${pending.name}`, { ttlMs: 6000 });
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  };

  // ctrl+q close-pane chord (stagelayout plan): close the currently-highlighted Stage pane. The
  // dispatcher only fires this when a Stage pane holds the effective focus, so this reads the effective
  // focus and routes by pane kind:
  //  - `stage:doc:<name>` → close the open doc via the docView action (rule 3). The pane unmounts →
  //    focus re-homes to chat via the derived invariant (no imperative re-home).
  //  - `stage:chat:<agentId>` → close that chat pane via `conversations.setChatPaneOpen(id, false)`.
  //    This writes an explicit `false` paneOverride that overrides the favorites default, so even a
  //    default-favorited collaborator/rogue pane disappears (a bare `toggleChatPane` would need the
  //    current open state; the explicit `false` is unconditional, which is what close means).
  const closePaneHandler = (): void => {
    const effective = selectEffectiveFocus(focus);
    if (effective.startsWith('stage:doc:')) {
      appStore.getState().actions.docView.close();
      return;
    }
    if (effective.startsWith('stage:chat:')) {
      const agentId = effective.slice('stage:chat:'.length);
      appStore.getState().actions.conversations.setChatPaneOpen(agentId, false);
    }
  };

  // Workstream E: the capability bag the chat-input prefix dispatcher (`commandDispatch`) needs.
  // Built here where the bus, modes, and the help/note handlers are all in scope — the dispatcher
  // stays a pure function over these capabilities (no store handles leak into it).
  //  - `captureNote` mirrors `quickNoteHandler`'s submit path (fire-and-forget, auto-titled).
  //  - `openHelp` reuses the `?` handler so `:help` and `?` open the identical overlay.
  //  - `dismiss` is omitted for v0: the panel architecture has no uniform "dismiss" concept yet, so
  //    `:dismiss` no-ops with a toast (see commandDispatch). Wire it when one exists.
  const commandCtx: CommandCtx = {
    sendKey: (agentId, key, literal, enter) => {
      void appStore.getState().actions.conversations.sendKey(agentId, key, literal, enter);
    },
    clearTranscript: (agentId) => {
      appStore.getState().actions.conversations.clearTranscript(agentId);
    },
    openHelp: keyHelpHandler,
    captureNote: (text) => {
      void submitCommand(bus, 'notetaker.capture.submit', { raw: text }).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
      toastStore.getState().push('note captured', { ttlMs: 6000 });
    },
    pushToast: (text, options) => toastStore.getState().push(text, options),
    saveTemplate: (name, body) => {
      void appStore.getState().actions.templates.save(name, body);
    },
  };

  // The single root input loop for the whole app (rule 5) — installed exactly once, here.
  // C13: `spawn` wired to the spawn wizard handler. C11: `chatInput` wired to the persistent
  // chat-input handler (buffers chars, sends on Enter to the active agent). Global ctrl-chords still
  // preempt it (layer 1 < layer 2), so the user can summon panels mid-message.
  useRootInput(
    {
      spawn: spawnHandler,
      openSettings: openSettingsHandler,
      newPlan: newPlanHandler,
      newTicket: newTicketHandler,
      quickNote: quickNoteHandler,
      keyHelp: keyHelpHandler,
      cycleTargetPrev: () => cycleTarget(-1),
      cycleTargetNext: () => cycleTarget(1),
      toggleTargetPane: toggleTargetPaneHandler,
      murder: murderHandler,
      murderPending: () => murderConfirmStore.getState().pending !== null,
      murderConfirm: murderConfirmHandler,
      murderCancel: () => murderConfirmStore.getState().clear(),
      closePane: closePaneHandler,
      chatInput: makeChatInputHandler(
        chatInput,
        appStore,
        imageDraft,
        commandCtx,
        chatHistory,
        chatVim,
        focus,
      ),
      // Mouse wheel while the chat input is focused scrolls the input's active send-target history
      // pane (when it's shown). Same target resolution the input border + send path use.
      chatScrollTargetAgentId: () => {
        const state = appStore.getState();
        return selectActiveAgentId(state.conversations, state.roster, state.favorites);
      },
    },
    terminalEvents,
  );

  // A full-screen mode (C14 tmux) replaces the whole layout: when one is active the shell renders
  // only the {@link Overlay} (which paints the full-viewport surface), suppressing its own bars and
  // panels. `modal`/`inlayout` modes keep the layout — the overlay draws over/within it. The
  // suppression predicate lives with the presentation data ({@link presentationHidesLayout}), not
  // hardcoded here, so a new full-screen-like presentation is honoured without editing the shell.
  useModeStore((s) => s.stack);
  const active = selectActiveMode(modes);
  // Min-terminal-size guard (first-run UX): below the floor the layout degenerates (rails + Stage
  // can't share 60-odd columns; modals clamp to ~24 wide; 16 rows barely fits chat + both bars), so
  // render a full-screen notice instead of a broken shell. Checked AFTER every hook (rules of
  // hooks) and BEFORE the fullscreen-mode return so a too-small terminal always shows the notice.
  // The non-TTY fallback (24×80, useTerminalSize) passes the floor, so piped/CI runs never trip it.
  if (columns < MIN_TERMINAL_COLUMNS || rows < MIN_TERMINAL_ROWS) {
    return (
      <Box
        flexDirection="column"
        width="100%"
        height={rows}
        overflow="hidden"
        justifyContent="center"
        alignItems="center"
      >
        <Text>terminal too small</Text>
        <Text
          dimColor
        >{`need ≥ ${MIN_TERMINAL_COLUMNS}×${MIN_TERMINAL_ROWS}, have ${columns}×${rows}`}</Text>
      </Box>
    );
  }
  if (active !== null && presentationHidesLayout(active.presentation)) {
    return <Overlay />;
  }
  // Item 4a: while a capturing (non-passThrough) mode is up, the chat input can't be typed into — it
  // owns input exclusively — so hide it. Its hints (and the chat field's role) move to the bottom
  // bar (item 4b). A `passThrough` mode (e.g. an inlayout editor that still lets chat work) keeps it.
  const chatInputHidden = active !== null && active.passThrough !== true;
  // Bound the whole app to the terminal height: a frame taller than the screen breaks Ink's in-place
  // redraw (it can only erase up to the screen height, so each re-render stacks a fresh full copy into
  // scrollback). This is the standard header/scroll/footer flex idiom:
  //  - root: fixed `height={rows}` + `overflow="hidden"` — the final safety clip, so nothing is ever
  //    drawn past the screen even if a child mis-measures.
  //  - chrome (top bar, chat, bottom bar): `flexShrink={0}` — keep their natural height, never squeezed.
  //  - middle region: `flexGrow={1} flexBasis={0}` — take *exactly* the remaining space (basis 0 so its
  //    tall content doesn't push the chrome out of the box), and `overflow="hidden"` clips the panels.
  // Without `flexBasis={0}` the region's basis is its (huge) content height, Yoga never bounds it, the
  // chrome is shoved past `rows`, and nothing clips — which is the "still way too tall" failure.
  return (
    <Box flexDirection="column" width="100%" height={rows} overflow="hidden">
      <Box ref={topbarRef} flexShrink={0} flexDirection="column">
        <TopBar project={project} />
      </Box>
      {/* The Body region: a fixed-height flex slot between the always-on TopBar and the bottom chrome.
          `flexGrow={1} flexBasis={0}` takes exactly the remaining height; `overflow="hidden"` clips.
          A `modal`/`inlayout` mode (item 4d) renders the {@link Overlay} centered INSIDE this slot —
          so the TopBar stays pinned at the top and the BottomBar at the bottom, the modal floating in
          the body — rather than the old float-up where the Overlay was a sibling AFTER the bottom
          chrome. When no mode is up the panels (rails + Stage) fill the slot. */}
      <Box flexGrow={1} flexBasis={0} minHeight={0} overflow="hidden" flexDirection="column">
        <BodyErrorBoundary>
          {active !== null ? (
            <Overlay />
          ) : (
            // Orientation-aware panels: landscape lays the rails + Stage out in a row (side-by-side),
            // portrait stacks them in a column.
            <Box
              flexDirection={orientation === 'landscape' ? 'row' : 'column'}
              columnGap={orientation === 'landscape' ? paneGap : 0}
              rowGap={orientation === 'portrait' ? paneGap : 0}
              flexGrow={1}
              flexBasis={0}
              minHeight={0}
              overflow="hidden"
            >
              <Rail
                side="left"
                orientation={orientation}
                panels={LEFT_PANELS}
                renderPanel={dispatchPanel}
                // Explicit, budget-computed cross-axis size — only as wide as its widest ledger row (R1/R2),
                // compressed (so trailing columns drop) when the Stage's 60% floor needs the room (R3).
                cells={bodyLayout.leftRailCells}
                // User-configured spacing between this rail's stacked/side-by-side panes.
                paneGap={paneGap}
              />
              {/* Phase 4a: the Stage center region — tiles the favorited-crow chat-history Panes, growing to
                fill whatever the rails leave (full width when both rails are off). It carries the budget
                floor (R3/R4) so it can never be sized below its guaranteed ≥60% share. Phase 4b adds
                doc-view panes to its right; the doc slice is untouched here. The Stage itself clips/grows. */}
              <Stage minCells={bodyLayout.stageCells} axis={bodyLayout.axis} paneGap={paneGap} />
              <Rail
                side="right"
                orientation={orientation}
                panels={RIGHT_PANELS}
                renderPanel={dispatchPanel}
                // The right rail (usage · crows) is sized to the crow-ledger width when crows are on (R6),
                // computed relative to the live terminal — no `"24%"` absolute anymore (R5).
                cells={bodyLayout.rightRailCells}
                // User-configured spacing between this rail's stacked/side-by-side panes.
                paneGap={paneGap}
              />
            </Box>
          )}
        </BodyErrorBoundary>
      </Box>
      <Box flexShrink={0} flexDirection="column">
        {!chatInputHidden && (
          <Box ref={chatInputRef} flexDirection="column">
            <ChatInput />
          </Box>
        )}
        <BottomBar />
      </Box>
      {/* F9: the transient toast rack — bottom-right, subtle. Last child so it rides below the bars;
          reads the toastStore singleton (pushed by the conversations send action + the image slice). */}
      <Toast />
    </Box>
  );
}

/**
 * The app root. Mounts the store + input-store + bus-client providers (all bundles injected as
 * props — rule 4) and renders the shell inside them. This is the whole composition: providers wrap,
 * {@link Shell} composes, panels fill in.
 *
 * `bus` is threaded here so the {@link TmuxFrame} component can open a transient subscription
 * via {@link BusClientProvider} / {@link useBusClient}. Actions remain the only domain-data path
 * (rule 3); the bus context is for streaming display data only (C14 tmux frames).
 */
export function App({
  store,
  inputStores,
  bus,
  project,
  terminalEvents,
}: {
  readonly store: AppStoreApi;
  readonly inputStores: InputStores;
  readonly bus: BusClient;
  /** Current project/repo name for the top-bar branding; from `MURDER_PROJECT` (see index.tsx). */
  readonly project?: string | undefined;
  /** The kitty stdin shim's chord channel (Phase 2), injected at the live entrypoint like `bus`.
   * Omitted in smoke/tests. */
  readonly terminalEvents?: TerminalEvents | undefined;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <BusClientProvider value={bus}>
          <Shell project={project} terminalEvents={terminalEvents} />
        </BusClientProvider>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}
